"""Unit tests for the briefing optimizer (scripts/optimize_briefing.py).

LLM calls are mocked; these pin the RL scaffolding: reward composition,
state-history prompt assembly, candidate parsing, and the budget stop.
"""

import pytest

from scripts.optimize_briefing import (
    Budget,
    Trial,
    build_optimizer_prompt,
    compose_reward,
    evaluate_briefing,
    parse_candidates,
)


class TestReward:
    def test_quality_minus_token_penalty(self):
        assert compose_reward(0.8, 10_000, 0.05) == 0.75
        assert compose_reward(0.8, 0, 0.05) == 0.8

    def test_shorter_output_wins_at_equal_quality(self):
        long_script = compose_reward(0.7, 20_000, 0.05)
        short_script = compose_reward(0.7, 5_000, 0.05)
        assert short_script > long_script


class TestBudget:
    def test_budget_exhaustion(self):
        b = Budget(max_total_tokens=100)
        assert not b.exhausted
        b.charge(60)
        assert not b.exhausted
        b.charge(40)
        assert b.exhausted


class TestOptimizerPrompt:
    def make_history(self):
        return [
            Trial(briefing="良い指示" * 30, reward=0.8, quality=0.85, tokens_out=5000),
            Trial(briefing="普通の指示" * 30, reward=0.6, quality=0.65, tokens_out=6000),
            Trial(briefing="悪い指示" * 30, reward=0.2, quality=0.25, tokens_out=9000),
            Trial(briefing="最悪の指示" * 30, reward=0.1, quality=0.15, tokens_out=9000),
        ]

    def test_prompt_contains_ranked_history_and_current(self):
        history = self.make_history()
        prompt = build_optimizer_prompt(history[0], history, k=3)
        assert "0.800" in prompt
        assert "[最低] 報酬 0.100" in prompt
        assert "3個" in prompt
        assert "良い指示" in prompt  # current briefing body

    def test_prompt_requests_json_actions(self):
        prompt = build_optimizer_prompt(self.make_history()[0], self.make_history(), k=2)
        assert '"rationale"' in prompt
        assert '"briefing"' in prompt


class TestParseCandidates:
    def test_parses_plain_json_array(self):
        text = '[{"rationale": "r1", "briefing": "b1"}, {"rationale": "r2", "briefing": "b2"}]'
        assert [c["briefing"] for c in parse_candidates(text)] == ["b1", "b2"]

    def test_parses_fenced_json_with_surrounding_prose(self):
        text = '編集案です。\n```json\n[{"rationale": "r", "briefing": "b"}]\n```\n以上。'
        assert parse_candidates(text)[0]["briefing"] == "b"

    def test_rejects_malformed_or_empty(self):
        assert parse_candidates("JSONなし") == []
        assert parse_candidates('[{"rationale": "r"}]') == []
        assert parse_candidates('[{"briefing": "  "}]') == []


class TestEvaluateBriefing:
    @pytest.mark.asyncio
    async def test_charges_budget_and_averages_quality(self, monkeypatch):
        import scripts.optimize_briefing as mod

        script = (
            "この章は、悩みに答えます。1つ目は結論。2つ目は根拠。3つ目は逆算。"
            "明日からのアクションプランです。実践しましょう。"
        )

        async def fake_generate(provider, model, briefing, content):
            return script, 1000, 2000

        monkeypatch.setattr(mod, "generate_with_model", fake_generate)
        budget = Budget(max_total_tokens=10_000)
        chapters = [
            {"chapter_index": 0, "content": "悩み。結論。根拠。逆算。実践。" * 20},
            {"chapter_index": 1, "content": "悩み。結論。根拠。逆算。実践。" * 20},
        ]
        trial = await evaluate_briefing(
            "briefing", chapters, ("anthropic", "m"), budget, token_penalty=0.05
        )
        assert budget.spent == 6000, "in+out tokens charged per chapter"
        assert trial.tokens_out == 2000, "per-chapter output tokens"
        assert 0.0 < trial.quality <= 1.0
        assert trial.reward == compose_reward(trial.quality, 2000, 0.05)


class TestOptimizeLoop:
    """optimize() orchestration with every LLM/DB surface mocked."""

    def make_args(self, tmp_path, **overrides):
        import argparse

        defaults = dict(
            audiobook="audiobook:a",
            profile="book_navigator",
            chapters=1,
            generations=2,
            beam=2,
            gen_model="anthropic:claude-haiku-4-5",
            token_penalty=0.05,
            max_tokens=1_000_000,
            out=str(tmp_path / "report.json"),
            apply=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def wire(self, monkeypatch, optimizer_response, scores):
        """Mock repo_query + generate_with_model. `scores` maps briefing text
        prefixes to structure-bearing scripts of different quality."""
        import scripts.optimize_briefing as mod

        queries = []

        async def fake_repo_query(q, binds=None):
            queries.append((q, binds))
            if "FROM episode_profile" in q and q.startswith("SELECT"):
                return [{"default_briefing": "BASE"}]
            if "FROM episode" in q:
                return [{"chapter_index": 0, "content": "悩み。結論。根拠。逆算。" * 60}]
            return []

        async def fake_generate(provider, model, briefing, content):
            if provider == "anthropic" and model == "claude-sonnet-5" and briefing == "":
                # optimizer call
                return optimizer_response, 500, 500
            # eval-generation call: quality depends on which briefing is used
            return scores.get(briefing, scores["BASE"]), 1000, 1500

        monkeypatch.setattr(mod, "repo_query", None, raising=False)
        monkeypatch.setattr(
            "open_notebook.database.repository.repo_query", fake_repo_query
        )
        monkeypatch.setattr(mod, "generate_with_model", fake_generate)
        return queries

    @pytest.mark.asyncio
    async def test_loop_improves_and_writes_report(self, monkeypatch, tmp_path):
        import json

        import scripts.optimize_briefing as mod

        good = (
            "この章は、悩みに答えます。1つ目は結論。2つ目は根拠。3つ目は逆算。"
            "明日からのアクションプランです。実践しましょう。"
        )
        optimizer_response = json.dumps(
            [
                {"rationale": "改善案", "briefing": "IMPROVED"},
                {"rationale": "別案", "briefing": "WORSE"},
            ],
            ensure_ascii=False,
        )
        self.wire(
            monkeypatch,
            optimizer_response,
            scores={"BASE": "短い。", "IMPROVED": good, "WORSE": "だめ。"},
        )
        args = self.make_args(tmp_path)
        await mod.optimize(args)

        report = json.loads((tmp_path / "report.json").read_text())
        assert report["best_briefing"] == "IMPROVED"
        assert report["best_reward"] > report["history"][0]["reward"]
        # baseline + 2 generations x 2 candidates evaluated
        assert len(report["history"]) == 5

    @pytest.mark.asyncio
    async def test_budget_stops_the_loop(self, monkeypatch, tmp_path):
        import json

        import scripts.optimize_briefing as mod

        self.wire(
            monkeypatch,
            '[{"rationale": "r", "briefing": "B2"}]',
            scores={"BASE": "短い。", "B2": "短い。"},
        )
        # Baseline eval costs 2500 tokens; cap right above it so generation 1
        # is never entered.
        args = self.make_args(tmp_path, max_tokens=2_600)
        await mod.optimize(args)
        report = json.loads((tmp_path / "report.json").read_text())
        assert len(report["history"]) == 1, "stopped before any candidate"

    @pytest.mark.asyncio
    async def test_unparseable_optimizer_output_is_skipped(self, monkeypatch, tmp_path):
        import json

        import scripts.optimize_briefing as mod

        self.wire(monkeypatch, "JSONではない返答", scores={"BASE": "短い。"})
        args = self.make_args(tmp_path, generations=1)
        await mod.optimize(args)
        report = json.loads((tmp_path / "report.json").read_text())
        assert len(report["history"]) == 1

    @pytest.mark.asyncio
    async def test_apply_writes_back_only_on_improvement(self, monkeypatch, tmp_path):
        import json

        import scripts.optimize_briefing as mod

        good = (
            "この章は、悩みに答えます。1つ目は結論。2つ目は根拠。3つ目は逆算。"
            "明日からのアクションプランです。実践しましょう。"
        )
        optimizer_response = json.dumps(
            [{"rationale": "r", "briefing": "IMPROVED"}], ensure_ascii=False
        )
        queries = self.wire(
            monkeypatch, optimizer_response, scores={"BASE": "短い。", "IMPROVED": good}
        )
        args = self.make_args(tmp_path, generations=1, apply=True)
        await mod.optimize(args)
        updates = [b for q, b in queries if q.startswith("UPDATE episode_profile")]
        assert updates and updates[0]["b"] == "IMPROVED"
