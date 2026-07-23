"""Mentor graph — 蔵書を完全に読み込んだ「師匠」との壁打ち (Book Navigator fork).

recall（過去の相談の記憶 + 蔵書横断ベクトル検索）→ respond（ペルソナ応答）→
memorize（今回の相談を長期記憶へ）の3ノード。ask がグローバル検索なのは
ここでは利点で、蔵書全体が師匠の知識になる。

Invoke:
    from open_notebook.graphs.mentor import graph
    result = await graph.ainvoke(
        {"message": "提案資料の構成を壁打ちしたい"},
        config={"configurable": {"mentor_model": "model:xxxx"}},
    )
    result["answer"]
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from loguru import logger

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content

# 記憶とヒットの注入上限（プロンプト肥大の抑制）
MAX_MEMORIES = 8
MAX_SEARCH_RESULTS = 5

PERSONA = """あなたは経験豊富な戦略コンサルタントの「師匠」です。相談者（弟子）の\
キャリアと仕事の質を引き上げることに責任を持っています。

原則:
- 結論から話し、必ず「次の一手」を具体的に示す
- 蔵書（下の検索結果）に根拠がある場合は「『本のタイトル』では〜」と出典を会話に織り込む
- 蔵書に無いことは一般論と明示して区別する。知らないことは知らないと言う
- 弟子の考えを頭ごなしに否定せず、まず良い点を認めてから改善点を1〜2個に絞って指摘する
- 過去の相談（下の記憶）と矛盾しない。以前の宿題や決定事項があればフォローアップする
- 敬体で、しかし率直に。長すぎる説教はしない"""


class MentorState(TypedDict):
    message: str
    memories: Annotated[list, operator.add]
    search_results: Annotated[list, operator.add]
    answer: str


def build_mentor_prompt(state: MentorState) -> str:
    """ペルソナ + 記憶 + 蔵書ヒット + 相談内容を1つのプロンプトへ。"""
    parts = [PERSONA, ""]
    if state.get("memories"):
        parts.append("## 過去の相談の記憶（新しい順）")
        for m in state["memories"][:MAX_MEMORIES]:
            parts.append(f"- {m.get('created', '')}: {m.get('question', '')} → {m.get('gist', '')}")
        parts.append("")
    if state.get("search_results"):
        parts.append("## 蔵書からの関連箇所")
        for hit in state["search_results"][:MAX_SEARCH_RESULTS]:
            title = hit.get("title") or hit.get("parent_id") or ""
            for chunk in (hit.get("matches") or [])[:2]:
                parts.append(f"- 『{title}』: {str(chunk)[:400]}")
        parts.append("")
    parts.append("## 今回の相談")
    parts.append(state["message"])
    return "\n".join(parts)


async def recall_node(state: MentorState, config: RunnableConfig) -> dict:
    """過去の記憶と蔵書の関連箇所を集める（失敗しても相談は続行）。"""
    from open_notebook.database.repository import repo_query
    from open_notebook.domain.notebook import vector_search

    memories: list = []
    hits: list = []
    try:
        memories = await repo_query(
            "SELECT type::string(created) AS created, question, gist "
            "FROM mentor_memory ORDER BY created DESC LIMIT $n",
            {"n": MAX_MEMORIES},
        )
    except Exception as e:  # noqa: BLE001 - memory is best-effort
        logger.warning(f"mentor memory recall failed: {e}")
    try:
        hits = await vector_search(
            state["message"], MAX_SEARCH_RESULTS, source=True, note=True
        )
    except Exception as e:  # noqa: BLE001 - search is best-effort
        logger.warning(f"mentor book search failed: {e}")
    return {"memories": memories, "search_results": hits}


async def respond_node(state: MentorState, config: RunnableConfig) -> dict:
    try:
        prompt = build_mentor_prompt(state)
        model = await provision_langchain_model(
            prompt,
            config.get("configurable", {}).get("mentor_model"),
            "chat",
            max_tokens=4000,
        )
        ai_message = await model.ainvoke(prompt)
        content = clean_thinking_content(extract_text_content(ai_message.content))
        return {"answer": content}
    except Exception as e:
        exc_class, message = classify_error(e)
        raise exc_class(message) from e


async def memorize_node(state: MentorState, config: RunnableConfig) -> dict:
    """相談の要点を長期記憶へ（余分なLLMコストをかけない素朴な要約）。"""
    from open_notebook.database.repository import repo_insert

    try:
        sources = sorted(
            {
                str(h.get("parent_id") or h.get("id"))
                for h in state.get("search_results", [])
                if h.get("parent_id") or h.get("id")
            }
        )
        await repo_insert(
            "mentor_memory",
            [
                {
                    "question": state["message"][:300],
                    "gist": (state.get("answer") or "")[:400],
                    "sources": sources or None,
                }
            ],
        )
    except Exception as e:  # noqa: BLE001 - memory write is best-effort
        logger.warning(f"mentor memorize failed: {e}")
    return {}


workflow = StateGraph(MentorState)
workflow.add_node("recall", recall_node)
workflow.add_node("respond", respond_node)
workflow.add_node("memorize", memorize_node)
workflow.add_edge(START, "recall")
workflow.add_edge("recall", "respond")
workflow.add_edge("respond", "memorize")
workflow.add_edge("memorize", END)

graph = workflow.compile()
