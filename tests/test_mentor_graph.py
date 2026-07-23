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
            AsyncMock(return_value=[{"title": "本", "matches": ["x"]}]),
        )
        out = await mentor.recall_node({"message": "相談"}, {})
        assert out["memories"][0]["question"] == "q"
        assert out["search_results"][0]["title"] == "本"

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
        assert out == {"memories": [], "search_results": []}


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
