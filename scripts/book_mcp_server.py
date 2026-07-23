"""Book Navigator MCP サーバー — 蔵書ナレッジを MCP クライアントへ公開する。

NotebookLM が Gemini から呼べるのと同様に、取り込んだ書籍を Claude Desktop /
Claude Code などの MCP クライアントから検索・質問できるようにする。

接続（Claude Code の例）:
    claude mcp add book-navigator -- \
        uv run --env-file /path/to/noteBoogie/.env \
        python /path/to/noteBoogie/scripts/book_mcp_server.py

前提: SurrealDB が起動済みで、書籍が ingest 済み（埋め込みあり）。
ask_book には default_chat_model の設定が必要（setup_book_navigator_models.py）。

Tools:
    list_books()                     蔵書一覧（Source）
    search_books(query, limit)       意味検索（チャンク+出典）
    ask_book(question)               検索+統合回答（引用付き、LLM使用）
    list_figures(source_id)          図・グラフのキャプション一覧
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    "book-navigator",
    instructions=(
        "取り込んだ書籍（ビジネス書など）のナレッジベース。"
        "まず list_books で蔵書を確認し、search_books で該当箇所を探すか、"
        "ask_book で本文グラウンディングされた回答を得る。"
    ),
)


# ---------------------------------------------------------------------------
# Tool bodies (plain async functions so tests can call them directly)
# ---------------------------------------------------------------------------


async def do_list_books() -> list[dict[str, Any]]:
    from open_notebook.database.repository import repo_query

    # ORDER BY here trips a SurrealDB "no iterator" error on this SDK; sort
    # client-side instead.
    rows = await repo_query(
        "SELECT type::string(id) AS id, title, string::len(full_text) AS chars "
        "FROM source"
    )
    return sorted(rows, key=lambda r: r.get("title") or "")


async def do_search_books(query: str, limit: int = 5) -> list[dict[str, Any]]:
    from open_notebook.domain.notebook import vector_search

    hits = await vector_search(query, limit, source=True, note=False)
    return [
        {
            "source_id": str(h.get("parent_id") or h.get("id")),
            "title": h.get("title"),
            "similarity": h.get("similarity"),
            "matches": h.get("matches"),
        }
        for h in hits
    ]


async def do_ask_book(question: str) -> str:
    from open_notebook.ai.models import DefaultModels
    from open_notebook.graphs.ask import graph

    defaults = await DefaultModels.get_instance()
    model_id = getattr(defaults, "default_chat_model", None)
    if not model_id:
        raise ValueError(
            "default_chat_model is not configured "
            "(run scripts/setup_book_navigator_models.py --set-defaults)"
        )
    model_id = str(model_id)
    result = await graph.ainvoke(  # type: ignore[call-overload]
        {"question": question},
        config={
            "configurable": {
                "strategy_model": model_id,
                "answer_model": model_id,
                "final_answer_model": model_id,
            }
        },
    )
    return result.get("final_answer") or "(no answer)"


async def do_list_figures(source_id: str) -> list[dict[str, Any]]:
    from open_notebook.database.repository import repo_query

    return await repo_query(
        "SELECT type::string(id) AS id, page, chapter_index, kind, caption "
        "FROM book_figure WHERE type::string(source) = $src ORDER BY page",
        {"src": source_id},
    )


# ---------------------------------------------------------------------------
# MCP tool registration (thin wrappers)
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_books() -> list[dict[str, Any]]:
    """取り込み済みの書籍（Source）一覧を返す。"""
    return await do_list_books()


@mcp.tool()
async def search_books(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """蔵書を意味検索し、一致チャンクと出典を返す。"""
    return await do_search_books(query, limit)


@mcp.tool()
async def ask_book(question: str) -> str:
    """蔵書ナレッジベースに質問し、本文グラウンディングされた回答を返す。

    回答には [source:...] 形式の出典が含まれる。
    """
    return await do_ask_book(question)


@mcp.tool()
async def list_figures(source_id: str) -> list[dict[str, Any]]:
    """書籍から分離された図・グラフの vision キャプション一覧を返す。"""
    return await do_list_figures(source_id)


def main() -> None:
    if "--selftest" in sys.argv:
        # DB 疎通と検索経路の確認（MCPクライアント無しで実行できる）。
        import asyncio

        async def selftest() -> None:
            books = await do_list_books()
            print(f"books: {len(books)}")
            for b in books[:3]:
                print(" ", b["id"], b["title"], f"{b['chars']:,} chars")
            if books:
                hits = await do_search_books("この本の要点", limit=2)
                print(f"search hits: {len(hits)}")
                figures = await do_list_figures(books[0]["id"])
                print(f"figures for first book: {len(figures)}")

        asyncio.run(selftest())
        return
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
