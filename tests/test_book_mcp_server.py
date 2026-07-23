"""Unit tests for the Book Navigator MCP server tool bodies."""

from unittest.mock import AsyncMock

import pytest

import scripts.book_mcp_server as srv


@pytest.mark.asyncio
async def test_list_books_sorts_by_title(monkeypatch):
    rows = [
        {"id": "source:b", "title": "戦略の本", "chars": 10},
        {"id": "source:a", "title": "コンサルの本", "chars": 20},
        {"id": "source:c", "title": None, "chars": 5},
    ]
    monkeypatch.setattr(
        "open_notebook.database.repository.repo_query", AsyncMock(return_value=rows)
    )
    books = await srv.do_list_books()
    assert [b["id"] for b in books] == ["source:c", "source:a", "source:b"]


@pytest.mark.asyncio
async def test_search_books_normalizes_hits(monkeypatch):
    hits = [
        {
            "id": "source:x",
            "parent_id": "source:x",
            "title": "本",
            "similarity": 0.91,
            "matches": ["チャンク1"],
        }
    ]
    monkeypatch.setattr(
        "open_notebook.domain.notebook.vector_search", AsyncMock(return_value=hits)
    )
    out = await srv.do_search_books("質問", limit=3)
    assert out == [
        {
            "source_id": "source:x",
            "title": "本",
            "similarity": 0.91,
            "matches": ["チャンク1"],
        }
    ]


@pytest.mark.asyncio
async def test_ask_book_requires_default_chat_model(monkeypatch):
    class Defaults:
        default_chat_model = None

    monkeypatch.setattr(
        "open_notebook.ai.models.DefaultModels.get_instance",
        AsyncMock(return_value=Defaults()),
    )
    with pytest.raises(ValueError, match="default_chat_model"):
        await srv.do_ask_book("質問")


@pytest.mark.asyncio
async def test_ask_book_invokes_graph_with_three_model_slots(monkeypatch):
    class Defaults:
        default_chat_model = "model:m1"

    monkeypatch.setattr(
        "open_notebook.ai.models.DefaultModels.get_instance",
        AsyncMock(return_value=Defaults()),
    )
    ainvoke = AsyncMock(return_value={"final_answer": "回答 [source:x]"})
    monkeypatch.setattr("open_notebook.graphs.ask.graph.ainvoke", ainvoke)

    answer = await srv.do_ask_book("Prismとは?")
    assert answer == "回答 [source:x]"
    cfg = ainvoke.call_args.kwargs["config"]["configurable"]
    assert (
        cfg["strategy_model"] == cfg["answer_model"] == cfg["final_answer_model"] == "model:m1"
    )


@pytest.mark.asyncio
async def test_list_figures_passes_source_filter(monkeypatch):
    q = AsyncMock(return_value=[{"id": "book_figure:f", "page": 3}])
    monkeypatch.setattr("open_notebook.database.repository.repo_query", q)
    out = await srv.do_list_figures("source:abc")
    assert out[0]["page"] == 3
    assert q.call_args.args[1] == {"src": "source:abc"}


def test_all_four_tools_are_registered():
    import asyncio

    tools = asyncio.run(srv.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"list_books", "search_books", "ask_book", "list_figures"}
