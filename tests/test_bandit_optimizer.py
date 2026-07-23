"""RL段階2: 離散オペレータ + Thompson sampling bandit のテスト。"""

import json
import random

from scripts.optimize_briefing_bandit import (
    LENGTH_HINTS,
    OPERATORS,
    ThompsonBandit,
    apply_set_length_hint,
    build_instantiation_prompt,
    load_bandit,
    log_episode,
    parse_single_edit,
    save_bandit,
)


class TestThompsonBandit:
    def test_update_moves_beta_parameters(self):
        bandit = ThompsonBandit()
        bandit.update("add_rule", success=True)
        bandit.update("add_rule", success=True)
        bandit.update("reorder", success=False)
        assert bandit.arms["add_rule"] == [3.0, 1.0]
        assert bandit.arms["reorder"] == [1.0, 2.0]

    def test_sample_prefers_the_learned_winner(self):
        bandit = ThompsonBandit(rng=random.Random(42))
        # add_rule が圧倒的に成功してきた世界
        bandit.arms["add_rule"] = [50.0, 1.0]
        for name in OPERATORS:
            if name != "add_rule":
                bandit.arms[name] = [1.0, 50.0]
        picks = [bandit.sample() for _ in range(20)]
        assert picks.count("add_rule") >= 18  # 探索の余地は残る（Thompson）

    def test_roundtrip_and_new_operator_tolerance(self):
        bandit = ThompsonBandit()
        bandit.update("rephrase_sentence", success=True)
        data = bandit.to_dict()
        # 保存後にオペレータが増えても既知分は引き継ぎ、新規は事前分布
        data["arms"].pop("reorder")
        restored = ThompsonBandit.from_dict(data)
        assert restored.arms["rephrase_sentence"] == [2.0, 1.0]
        assert restored.arms["reorder"] == [1.0, 1.0]

    def test_load_bandit_survives_corrupt_state(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{broken")
        bandit = load_bandit(path)
        assert bandit.arms["add_rule"] == [1.0, 1.0]

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "rl" / "state.json"
        bandit = ThompsonBandit()
        bandit.update("remove_sentence", success=False)
        save_bandit(bandit, path)
        assert load_bandit(path).arms["remove_sentence"] == [1.0, 2.0]


class TestOperators:
    def test_set_length_hint_noop_in_band(self):
        assert apply_set_length_hint("元のbriefing。", 4.0) is None

    def test_set_length_hint_shortens_bloated_transcripts(self):
        edited = apply_set_length_hint("元のbriefing。", 12.0)
        assert edited is not None and LENGTH_HINTS["shorter"] in edited

    def test_set_length_hint_replaces_previous_hint(self):
        briefing = "元のbriefing。" + LENGTH_HINTS["shorter"]
        edited = apply_set_length_hint(briefing, 0.5)
        assert edited is not None
        assert LENGTH_HINTS["longer"] in edited
        assert LENGTH_HINTS["shorter"] not in edited  # 付替えであって併記ではない

    def test_instantiation_prompt_scopes_the_edit(self):
        prompt = build_instantiation_prompt("remove_sentence", "briefing本文", "grounding=0.60")
        assert OPERATORS["remove_sentence"] in prompt
        assert "grounding=0.60" in prompt
        assert "briefing本文" in prompt

    def test_parse_single_edit(self):
        good = '前置き\n```json\n{"rationale": "簡潔化", "briefing": "編集後"}\n```'
        assert parse_single_edit(good) == {"rationale": "簡潔化", "briefing": "編集後"}
        assert parse_single_edit("JSONなし") is None
        assert parse_single_edit('{"briefing": ""}') is None


def test_log_episode_appends_jsonl(tmp_path):
    path = tmp_path / "rl" / "log.jsonl"
    log_episode(path, {"action": "add_rule", "success": True})
    log_episode(path, {"action": "reorder", "success": False})
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action"] == "add_rule"


# ---------------------------------------------------------------------------
# optimize() ループ（LLM・DBモックの統合テスト）
# ---------------------------------------------------------------------------

import argparse
import asyncio
from unittest.mock import patch

GOOD = (
    "この章は仮説思考が身につかない悩みに答えます。"
    "1つ目は仮説思考です。結論から考える技術です。"
    "2つ目は論点思考です。3つ目はイシュー分析です。"
    "アクションプランです。明日から実践してみてください。"
) * 3
BAD = "量子コンピュータの話だ。"
CONTENT = ("仮説思考とは結論から考える技術である。論点思考。イシュー分析。実践。" * 30)


def test_optimize_loop_learns_and_persists(tmp_path, monkeypatch):
    from scripts import optimize_briefing_bandit as bandit_mod

    async def fake_repo_query(query, params=None):
        # 注意: "FROM episode_profile" は "FROM episode" を含むので順序が重要
        if "SELECT default_briefing" in query:
            return [{"default_briefing": "元のbriefing。"}]
        if "UPDATE episode_profile" in query:
            fake_repo_query.applied = params
            return []
        if "FROM episode" in query:
            return [{"chapter_index": 0, "content": CONTENT, "content_len": len(CONTENT)}]
        raise AssertionError(query)

    fake_repo_query.applied = None

    calls = {"n": 0}

    async def fake_generate(provider, model, briefing, content):
        # instantiation LLM 呼び出し（briefing==""）は編集JSONを返す
        if briefing == "":
            return ('{"rationale": "ルール追加", "briefing": "編集後のbriefing。"}', 100, 50)
        # 台本生成: baseline は悪い台本、編集後は良い台本
        calls["n"] += 1
        text = BAD if "元の" in briefing else GOOD
        return (text, 500, 300)

    import open_notebook.database.repository as repo_mod

    monkeypatch.setattr(repo_mod, "repo_query", fake_repo_query)
    monkeypatch.setattr(bandit_mod, "generate_with_model", fake_generate)

    args = argparse.Namespace(
        audiobook="audiobook:x", profile="book_navigator", chapters=1, steps=2,
        gen_model="anthropic:claude-haiku-4-5", token_penalty=0.05,
        max_tokens=1_000_000, state=str(tmp_path / "state.json"),
        log=str(tmp_path / "log.jsonl"), apply=True,
    )
    with patch.object(bandit_mod.ThompsonBandit, "sample", return_value="add_rule"):
        asyncio.run(bandit_mod.optimize(args))

    # 学習状態が永続化され、成功が α に反映されている
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["arms"]["add_rule"][0] > 1.0
    # (状態, 行動, 報酬) ログが残る
    lines = (tmp_path / "log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["action"] == "add_rule" and first["success"] is True
    # --apply で改善後 briefing が書き戻される
    assert fake_repo_query.applied is not None
    assert fake_repo_query.applied["b"] == "編集後のbriefing。"
