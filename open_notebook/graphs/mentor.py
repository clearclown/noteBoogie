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

# ペルソナは設定可能（migration 31 の mentor_profile、既定シードはコンサル）。
# コード側フォールバックはドメイン非依存 — このプロダクトは特定職種向けではなく、
# 「自分の蔵書を読み込んだ師匠」を任意の分野で成立させる（NotebookLM 上位互換）。
DEFAULT_PERSONA = """あなたは蔵書（下の検索結果の出典となる本・資料）を深く読み込んだ、\
経験豊富な「師匠」です。相談者（弟子）の成長と仕事の質を引き上げることに責任を持っています。"""

# 原則はペルソナ非依存の品質規範（どの分野の師匠でも共通）
PRINCIPLES = """原則:
- 結論から話し、必ず「次の一手」を具体的に示す
- 蔵書（下の検索結果）に根拠がある場合は「『本のタイトル』では〜」と出典を会話に織り込む
- 蔵書に無いことは一般論と明示して区別する。知らないことは知らないと言う
- 弟子の考えを頭ごなしに否定せず、まず良い点を認めてから改善点を1〜2個に絞って指摘する
- 過去の相談（下の記憶）と矛盾しない。以前の宿題や決定事項があればフォローアップする
- 敬体で、しかし率直に。長すぎる説教はしない"""


async def load_persona() -> str:
    """アクティブなペルソナを読む（未設定・DB不通時はドメイン非依存の既定）。

    切替は mentor_profile.active（migration 32）。コンサル（name='default'）が
    初期アクティブだが、engineer/editor などのプリセットや自作に切替できる。
    """
    from open_notebook.database.repository import repo_query

    try:
        rows = await repo_query(
            "SELECT persona FROM mentor_profile WHERE active = true LIMIT 1"
        )
        if not rows:
            rows = await repo_query(
                "SELECT persona FROM mentor_profile WHERE name = 'default'"
            )
        if rows and rows[0].get("persona"):
            return str(rows[0]["persona"])
    except Exception as e:  # noqa: BLE001 - persona is best-effort config
        logger.warning(f"mentor persona load failed: {e}")
    return DEFAULT_PERSONA


class MentorState(TypedDict, total=False):
    message: str
    memories: Annotated[list, operator.add]
    search_results: Annotated[list, operator.add]
    answer: str
    # Self-RAG: 蔵書に十分な根拠が無かったことの決定的フラグ（recall が設定）
    low_evidence: bool


def mentor_evidence_floor() -> float:
    import os

    try:
        return float(os.getenv("MENTOR_EVIDENCE_FLOOR", "0.4"))
    except ValueError:
        return 0.4


def build_mentor_prompt(state: MentorState, persona: str | None = None) -> str:
    """ペルソナ + 原則 + 記憶 + 蔵書ヒット + 相談内容を1つのプロンプトへ。"""
    parts = [persona or DEFAULT_PERSONA, "", PRINCIPLES, ""]
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
    if state.get("low_evidence"):
        # プロンプト頼みではなく検索スコアの決定的分岐（ADVANCED_ROADMAP §4-2）
        parts.append(
            "## 蔵書検索の結果\n"
            "今回の相談に十分に関連する蔵書の記述は見つからなかった（機械判定）。\n"
            "一般論として助言し、回答の冒頭で「蔵書に直接の記述はありませんが」と明示すること。\n"
            "蔵書からの引用（『本のタイトル』では〜）を捏造しないこと。"
        )
        parts.append("")
    parts.append("## 今回の相談")
    parts.append(state["message"])
    return "\n".join(parts)


# --- 蔵書の傾斜（学習の重み付け） -------------------------------------------

# 自動傾斜: 直近の相談でよく参照された本を緩やかに重くする
AUTO_WEIGHT_ALPHA = 0.15
AUTO_WEIGHT_CAP = 1.5


def compute_auto_factors(recent_memory_sources: list) -> dict:
    """直近の mentor_memory.sources から本ごとの自動係数を算出する。

    factor = min(1 + α·ln(1 + 参照回数), 上限)。ユーザー設定（手動傾斜）とは
    掛け算で合成され、読み取り専用のシグナルとして働く。
    """
    import math

    counts: dict = {}
    for sources in recent_memory_sources:
        for source_id in sources or []:
            counts[source_id] = counts.get(source_id, 0) + 1
    return {
        source_id: min(1.0 + AUTO_WEIGHT_ALPHA * math.log1p(count), AUTO_WEIGHT_CAP)
        for source_id, count in counts.items()
    }


def apply_weights(hits: list, manual: dict, auto: dict) -> list:
    """検索ヒットに 手動×自動 の傾斜を掛けて再ランクする。

    - manual: {source_id: weight 0.0〜2.0}（未設定は1.0）
    - weight 0.0 の本は除外（「この本からは学ばない」）
    - similarity を effective 倍して降順に並べ替え
    """
    weighted = []
    for hit in hits:
        source_id = str(hit.get("parent_id") or hit.get("id") or "")
        manual_w = manual.get(source_id, 1.0)
        if manual_w <= 0.0:
            continue
        effective = manual_w * auto.get(source_id, 1.0)
        score = float(hit.get("similarity") or 0.0) * effective
        weighted.append(({**hit, "weighted_score": round(score, 4)}, score))
    weighted.sort(key=lambda pair: pair[1], reverse=True)
    return [hit for hit, _ in weighted]


async def load_manual_weights() -> dict:
    from open_notebook.database.repository import repo_query

    rows = await repo_query(
        "SELECT type::string(source) AS source_id, weight FROM mentor_source_weight"
    )
    return {r["source_id"]: float(r.get("weight", 1.0)) for r in rows}


async def recall_node(state: MentorState, config: RunnableConfig) -> dict:
    """過去の記憶と蔵書の関連箇所を集める（失敗しても相談は続行）。"""
    from open_notebook.database.repository import repo_query
    from open_notebook.domain.notebook import vector_search

    memories: list = []
    hits: list = []
    try:
        memories = await repo_query(
            "SELECT type::string(created) AS created, question, gist, sources "
            "FROM mentor_memory ORDER BY created DESC LIMIT $n",
            {"n": MAX_MEMORIES},
        )
    except Exception as e:  # noqa: BLE001 - memory is best-effort
        logger.warning(f"mentor memory recall failed: {e}")
    try:
        # 傾斜を効かせるため、上限より広めに取ってから再ランクで絞る
        raw_hits = await vector_search(
            state["message"], MAX_SEARCH_RESULTS * 3, source=True, note=True
        )
        manual: dict = {}
        try:
            manual = await load_manual_weights()
        except Exception as e:  # noqa: BLE001 - weights are best-effort
            logger.warning(f"mentor weight load failed: {e}")
        auto = compute_auto_factors([m.get("sources") for m in memories])
        hits = apply_weights(raw_hits, manual, auto)[:MAX_SEARCH_RESULTS]
    except Exception as e:  # noqa: BLE001 - search is best-effort
        logger.warning(f"mentor book search failed: {e}")

    # Self-RAG: 生の類似度が下限未満なら「根拠なし」と決定的に判定し、
    # 薄いヒットをプロンプトに流し込まない（引用捏造の入口を塞ぐ）
    floor = mentor_evidence_floor()
    top = max((float(h.get("similarity") or 0.0) for h in hits), default=0.0)
    low_evidence = top < floor
    if low_evidence:
        from open_notebook.utils.quality_events import log_quality_event

        await log_quality_event(
            kind="mentor_low_evidence",
            name=state["message"][:200],
            score=top,
            verdict="low_evidence",
            details={"floor": floor, "hits": len(hits)},
        )
        hits = []
    return {"memories": memories, "search_results": hits, "low_evidence": low_evidence}


async def respond_node(state: MentorState, config: RunnableConfig) -> dict:
    try:
        prompt = build_mentor_prompt(state, persona=await load_persona())
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
