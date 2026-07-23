"""Unit tests for the mentor graph (open_notebook/graphs/mentor.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from open_notebook.graphs import mentor


class TestBuildMentorPrompt:
    def test_full_prompt_contains_persona_memories_sources_and_message(self):
        state = {
            "message": "提案資料の構成を壁打ちしたい",
            "memories": [
                {"created": "2026-07-20", "question": "報告の悩み", "gist": "結論から話す"},
            ],
            "search_results": [
                {"title": "コンサル頭のつくり方", "matches": ["論点思考とは…", "二つ目のチャンク"]},
            ],
        }
        prompt = mentor.build_mentor_prompt(state)
        assert "師匠" in prompt
        assert "報告の悩み" in prompt and "結論から話す" in prompt
        assert "『コンサル頭のつくり方』" in prompt
        assert "提案資料の構成を壁打ちしたい" in prompt

    def test_empty_recall_omits_sections(self):
        prompt = mentor.build_mentor_prompt(
            {"message": "相談", "memories": [], "search_results": []}
        )
        assert "過去の相談の記憶" not in prompt
        assert "蔵書からの関連箇所" not in prompt
        assert "## 今回の相談" in prompt

    def test_injection_limits_are_enforced(self):
        state = {
            "message": "m",
            "memories": [{"question": f"q{i}", "gist": "g"} for i in range(20)],
            "search_results": [
                {"title": f"本{i}", "matches": ["c1", "c2", "c3"]} for i in range(10)
            ],
        }
        prompt = mentor.build_mentor_prompt(state)
        assert f"q{mentor.MAX_MEMORIES - 1}" in prompt
        assert f"q{mentor.MAX_MEMORIES}" not in prompt
        assert f"本{mentor.MAX_SEARCH_RESULTS - 1}" in prompt
        assert f"本{mentor.MAX_SEARCH_RESULTS}" not in prompt
        # 2 chunks max per hit
        assert "c3" not in prompt


class TestRecallNode:
    @pytest.mark.asyncio
    async def test_collects_memories_and_hits(self, monkeypatch):
        monkeypatch.setattr(
            "open_notebook.database.repository.repo_query",
            AsyncMock(return_value=[{"question": "q", "gist": "g"}]),
        )
        monkeypatch.setattr(
            "open_notebook.domain.notebook.vector_search",
            AsyncMock(return_value=[{"title": "本", "matches": ["x"], "similarity": 0.8}]),
        )
        out = await mentor.recall_node({"message": "相談"}, {})
        assert out["memories"][0]["question"] == "q"
        assert out["search_results"][0]["title"] == "本"
        assert out["low_evidence"] is False

    @pytest.mark.asyncio
    async def test_survives_db_and_search_failures(self, monkeypatch):
        monkeypatch.setattr(
            "open_notebook.database.repository.repo_query",
            AsyncMock(side_effect=RuntimeError("db down")),
        )
        monkeypatch.setattr(
            "open_notebook.domain.notebook.vector_search",
            AsyncMock(side_effect=RuntimeError("no embeddings")),
        )
        out = await mentor.recall_node({"message": "相談"}, {})
        # 検索が全滅した場合も落ちず、根拠なしフラグ付きで続行する
        assert out["memories"] == [] and out["search_results"] == []
        assert out["low_evidence"] is True

    @pytest.mark.asyncio
    async def test_low_similarity_sets_low_evidence_and_drops_hits(self, monkeypatch):
        """Self-RAG: 下限未満のヒットはプロンプトに流さない（引用捏造の入口を塞ぐ）。"""
        monkeypatch.setattr(
            "open_notebook.database.repository.repo_query",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "open_notebook.domain.notebook.vector_search",
            AsyncMock(return_value=[{"title": "本", "similarity": 0.25}]),
        )
        log = AsyncMock()
        monkeypatch.setattr(
            "open_notebook.utils.quality_events.log_quality_event", log
        )
        out = await mentor.recall_node({"message": "無関係な相談"}, {})
        assert out["low_evidence"] is True
        assert out["search_results"] == []
        assert log.await_args.kwargs["kind"] == "mentor_low_evidence"

    @pytest.mark.asyncio
    async def test_floor_is_env_tunable(self, monkeypatch):
        monkeypatch.setenv("MENTOR_EVIDENCE_FLOOR", "0.2")
        monkeypatch.setattr(
            "open_notebook.database.repository.repo_query",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "open_notebook.domain.notebook.vector_search",
            AsyncMock(return_value=[{"title": "本", "similarity": 0.25}]),
        )
        out = await mentor.recall_node({"message": "相談"}, {})
        assert out["low_evidence"] is False
        assert out["search_results"][0]["title"] == "本"

    def test_low_evidence_prompt_forbids_fabricated_citations(self):
        prompt = mentor.build_mentor_prompt(
            {"message": "相談", "memories": [], "search_results": [], "low_evidence": True}
        )
        assert "蔵書に直接の記述はありませんが" in prompt
        assert "捏造しない" in prompt
        # 通常時はこのセクションが入らない
        normal = mentor.build_mentor_prompt(
            {"message": "相談", "memories": [], "search_results": []}
        )
        assert "蔵書に直接の記述はありませんが" not in normal


class TestRespondNode:
    @pytest.mark.asyncio
    async def test_uses_configured_model_and_cleans_output(self, monkeypatch):
        ai_message = MagicMock()
        ai_message.content = "<think>内心</think>結論から言います。"
        model = MagicMock()
        model.ainvoke = AsyncMock(return_value=ai_message)
        provision = AsyncMock(return_value=model)
        monkeypatch.setattr(mentor, "provision_langchain_model", provision)

        out = await mentor.respond_node(
            {"message": "相談", "memories": [], "search_results": []},
            {"configurable": {"mentor_model": "model:m1"}},
        )
        assert out["answer"] == "結論から言います。"
        assert provision.call_args.args[1] == "model:m1"


class TestMemorizeNode:
    @pytest.mark.asyncio
    async def test_stores_truncated_gist_and_sources(self, monkeypatch):
        insert = AsyncMock()
        monkeypatch.setattr("open_notebook.database.repository.repo_insert", insert)
        await mentor.memorize_node(
            {
                "message": "M" * 500,
                "answer": "A" * 900,
                "search_results": [
                    {"parent_id": "source:b"},
                    {"parent_id": "source:a"},
                    {"parent_id": "source:a"},
                ],
            },
            {},
        )
        record = insert.call_args.args[1][0]
        assert len(record["question"]) == 300
        assert len(record["gist"]) == 400
        assert record["sources"] == ["source:a", "source:b"]

    @pytest.mark.asyncio
    async def test_write_failure_never_breaks_the_conversation(self, monkeypatch):
        monkeypatch.setattr(
            "open_notebook.database.repository.repo_insert",
            AsyncMock(side_effect=RuntimeError("db down")),
        )
        out = await mentor.memorize_node(
            {"message": "m", "answer": "a", "search_results": []}, {}
        )
        assert out == {}


def test_graph_wiring():
    nodes = set(mentor.graph.get_graph().nodes)
    assert {"recall", "respond", "memorize"} <= nodes


# ---------------------------------------------------------------------------
# 蔵書の傾斜（manual x auto weighting）
# ---------------------------------------------------------------------------


class TestAutoFactors:
    def test_frequency_raises_factor_with_cap(self):
        import math

        sources = [["source:a"], ["source:a", "source:b"], ["source:a"], None]
        factors = mentor.compute_auto_factors(sources)
        assert factors["source:a"] == pytest.approx(
            min(1 + mentor.AUTO_WEIGHT_ALPHA * math.log1p(3), mentor.AUTO_WEIGHT_CAP)
        )
        assert factors["source:b"] < factors["source:a"]
        assert "source:c" not in factors

    def test_cap_is_enforced(self):
        sources = [["source:hot"]] * 1000
        factors = mentor.compute_auto_factors(sources)
        assert factors["source:hot"] == mentor.AUTO_WEIGHT_CAP


class TestApplyWeights:
    HITS = [
        {"parent_id": "source:a", "title": "A", "similarity": 0.80},
        {"parent_id": "source:b", "title": "B", "similarity": 0.78},
        {"parent_id": "source:c", "title": "C", "similarity": 0.75},
    ]

    def test_default_weights_keep_similarity_order(self):
        out = mentor.apply_weights(self.HITS, {}, {})
        assert [h["parent_id"] for h in out] == ["source:a", "source:b", "source:c"]
        assert out[0]["weighted_score"] == pytest.approx(0.80)

    def test_manual_weight_rerannks(self):
        # ユーザーが C を最重視（2.0）、A を軽視（0.5）
        out = mentor.apply_weights(
            self.HITS, {"source:c": 2.0, "source:a": 0.5}, {}
        )
        assert [h["parent_id"] for h in out] == ["source:c", "source:b", "source:a"]

    def test_zero_weight_excludes_the_book(self):
        out = mentor.apply_weights(self.HITS, {"source:b": 0.0}, {})
        assert [h["parent_id"] for h in out] == ["source:a", "source:c"]

    def test_auto_factor_composes_multiplicatively(self):
        out = mentor.apply_weights(
            self.HITS, {"source:b": 1.2}, {"source:b": 1.3}
        )
        assert out[0]["parent_id"] == "source:b"
        assert out[0]["weighted_score"] == pytest.approx(0.78 * 1.2 * 1.3, abs=1e-3)


class TestWeightedRecall:
    @pytest.mark.asyncio
    async def test_recall_applies_weights_and_truncates(self, monkeypatch):
        async def fake_repo_query(q, binds=None):
            if "mentor_source_weight" in q:
                return [{"source_id": "source:fav", "weight": 2.0}]
            # mentor_memory: source:fav was referenced before -> auto boost too
            return [{"question": "q", "gist": "g", "sources": ["source:fav"]}]

        raw = [
            {"parent_id": f"source:{i}", "similarity": 0.9 - i * 0.01}
            for i in range(10)
        ] + [{"parent_id": "source:fav", "similarity": 0.5}]
        monkeypatch.setattr(
            "open_notebook.database.repository.repo_query", fake_repo_query
        )
        monkeypatch.setattr(
            "open_notebook.domain.notebook.vector_search",
            AsyncMock(return_value=raw),
        )
        out = await mentor.recall_node({"message": "相談"}, {})
        hits = out["search_results"]
        assert len(hits) == mentor.MAX_SEARCH_RESULTS, "truncated after rerank"
        # 0.5 similarity but 2.0 manual x auto boost -> top of the list
        assert hits[0]["parent_id"] == "source:fav"

    @pytest.mark.asyncio
    async def test_weight_load_failure_falls_back_to_neutral(self, monkeypatch):
        call_count = {"n": 0}

        async def flaky_repo_query(q, binds=None):
            if "mentor_source_weight" in q:
                raise RuntimeError("table missing")
            return []

        monkeypatch.setattr(
            "open_notebook.database.repository.repo_query", flaky_repo_query
        )
        monkeypatch.setattr(
            "open_notebook.domain.notebook.vector_search",
            AsyncMock(return_value=[{"parent_id": "source:a", "similarity": 0.9}]),
        )
        out = await mentor.recall_node({"message": "相談"}, {})
        assert out["search_results"][0]["parent_id"] == "source:a"
