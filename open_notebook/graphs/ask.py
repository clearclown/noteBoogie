import operator
from typing import Annotated, List

from ai_prompter import Prompter
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.domain.notebook import vector_search
from open_notebook.exceptions import OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.quality_events import log_quality_event
from open_notebook.utils.text_utils import extract_text_content

# --- Self-RAG refusal (Book Navigator, ADVANCED_ROADMAP §4-2) ---------------
# 検索スコアによる決定的な「答えられない」判定。プロンプト頼みにせず、
# 類似度の下限を割った検索は生成LLMを呼ばずに根拠不足と申告する。
REFUSAL_PREFIX = "（根拠不足）"
NO_EVIDENCE_ANSWER = (
    "蔵書に十分な根拠が見つかりませんでした。"
    "別の聞き方にするか、関連する本を取り込んでから再度お試しください。"
)


def ask_evidence_floor() -> float:
    import os

    try:
        return float(os.getenv("ASK_EVIDENCE_FLOOR", "0.4"))
    except ValueError:
        return 0.4


def top_similarity(results: list) -> float:
    return max((float(r.get("similarity") or 0.0) for r in results), default=0.0)


async def notebook_member_ids(notebook_id: str) -> set:
    """notebook に属する source/note の id 集合（ask の notebook スコープ用）。"""
    from open_notebook.database.repository import repo_query

    members: set = set()
    for relation in ("reference", "artifact"):  # source→notebook / note→notebook
        rows = await repo_query(
            f"SELECT VALUE type::string(in) FROM {relation} WHERE out = type::thing($nb)",
            {"nb": notebook_id},
        )
        members.update(str(r) for r in rows)
    return members


def filter_to_notebook(results: list, member_ids: set) -> list:
    """ヒットを notebook 所属の親（source/note）に絞る後段フィルタ。"""
    return [r for r in results if str(r.get("parent_id") or "") in member_ids]


class SubGraphState(TypedDict):
    question: str
    term: str
    instructions: str
    results: dict
    answer: str
    ids: list  # Added for provide_answer function


class Search(BaseModel):
    term: str
    instructions: str = Field(
        description="Tell the answeting LLM what information you need extracted from this search"
    )


class Strategy(BaseModel):
    reasoning: str
    searches: List[Search] = Field(
        default_factory=list,
        description="You can add up to five searches to this strategy",
    )


class ThreadState(TypedDict):
    question: str
    strategy: Strategy
    answers: Annotated[list, operator.add]
    final_answer: str


async def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        parser: PydanticOutputParser[Strategy] = PydanticOutputParser(
            pydantic_object=Strategy
        )
        system_prompt = Prompter(prompt_template="ask/entry", parser=parser).render(  # type: ignore[arg-type]
            data=state  # type: ignore[arg-type]
        )
        model = await provision_langchain_model(
            system_prompt,
            config.get("configurable", {}).get("strategy_model"),
            "tools",
            max_tokens=2000,
            structured=dict(type="json"),
        )
        # model = model.bind_tools(tools)
        # First get the raw response from the model
        ai_message = await model.ainvoke(system_prompt)

        # Clean the thinking content from the response
        message_content = extract_text_content(ai_message.content)
        cleaned_content = clean_thinking_content(message_content)

        # Parse the cleaned JSON content
        strategy = parser.parse(cleaned_content)

        return {"strategy": strategy}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


async def trigger_queries(state: ThreadState, config: RunnableConfig):
    return [
        Send(
            "provide_answer",
            {
                "question": state["question"],
                "instructions": s.instructions,
                "term": s.term,
                # "type": s.type,
            },
        )
        for s in state["strategy"].searches
    ]


async def provide_answer(state: SubGraphState, config: RunnableConfig) -> dict:
    try:
        payload = state
        # if state["type"] == "text":
        #     results = text_search(state["term"], 10, True, True)
        # else:
        results = await vector_search(state["term"], 10, True, True)
        # notebook スコープ指定時は所属 source/note に絞る（グローバル検索の限界対応）。
        # 根拠不足判定より先に絞ることで、refusal はスコープ内の根拠を反映する
        notebook_id = config.get("configurable", {}).get("notebook_id")
        if notebook_id:
            results = filter_to_notebook(
                results, await notebook_member_ids(str(notebook_id))
            )
        floor = ask_evidence_floor()
        top = top_similarity(results)
        if len(results) == 0 or top < floor:
            # 決定的分岐: 根拠が薄い検索は回答LLMを呼ばない（捏造の入口を塞ぐ）
            await log_quality_event(
                kind="ask_refusal",
                name=state["term"],
                score=top,
                verdict="refused",
                details={"floor": floor, "hits": len(results)},
            )
            return {
                "answers": [
                    f"{REFUSAL_PREFIX}検索語「{state['term']}」では蔵書に"
                    f"十分な根拠が見つかりませんでした（最大類似度 {top:.2f}）。"
                ]
            }
        payload["results"] = results
        ids = [r["id"] for r in results]
        payload["ids"] = ids
        system_prompt = Prompter(prompt_template="ask/query_process").render(data=payload)  # type: ignore[arg-type]
        model = await provision_langchain_model(
            system_prompt,
            config.get("configurable", {}).get("answer_model"),
            "tools",
            max_tokens=2000,
        )
        ai_message = await model.ainvoke(system_prompt)
        ai_content = extract_text_content(ai_message.content)
        return {"answers": [clean_thinking_content(ai_content)]}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


async def write_final_answer(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        answers = state.get("answers") or []
        # 全検索が根拠不足なら、統合LLMも呼ばず定型文で正直に断る（Self-RAG）
        if answers and all(str(a).startswith(REFUSAL_PREFIX) for a in answers):
            return {"final_answer": NO_EVIDENCE_ANSWER}
        system_prompt = Prompter(prompt_template="ask/final_answer").render(data=state)  # type: ignore[arg-type]
        model = await provision_langchain_model(
            system_prompt,
            config.get("configurable", {}).get("final_answer_model"),
            "tools",
            max_tokens=2000,
        )
        ai_message = await model.ainvoke(system_prompt)
        final_content = extract_text_content(ai_message.content)
        return {"final_answer": clean_thinking_content(final_content)}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_node("provide_answer", provide_answer)
agent_state.add_node("write_final_answer", write_final_answer)
agent_state.add_edge(START, "agent")
agent_state.add_conditional_edges("agent", trigger_queries, ["provide_answer"])
agent_state.add_edge("provide_answer", "write_final_answer")
agent_state.add_edge("write_final_answer", END)

graph = agent_state.compile()
