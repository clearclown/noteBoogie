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
