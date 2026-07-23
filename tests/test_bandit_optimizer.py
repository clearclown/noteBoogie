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
