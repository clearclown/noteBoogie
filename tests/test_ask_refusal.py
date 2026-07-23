"""Self-RAG refusal branch in the ask graph (ADVANCED_ROADMAP §4-2).

検索スコアが下限未満のとき、回答LLMを呼ばずに決定的に「根拠不足」を返すこと。
"""

from unittest.mock import AsyncMock, patch

import pytest

from open_notebook.graphs.ask import (
    NO_EVIDENCE_ANSWER,
    REFUSAL_PREFIX,
    ask_evidence_floor,
    provide_answer,
    top_similarity,
    write_final_answer,
)

HIGH_HITS = [
    {"id": "source_embedding:1", "parent_id": "source:a", "title": "本A",
     "content": "仮説思考とは結論から考える技術", "similarity": 0.72},
    {"id": "source_embedding:2", "parent_id": "source:a", "title": "本A",
     "content": "論点思考は問いを絞る", "similarity": 0.61},
]

LOW_HITS = [
    {"id": "source_embedding:3", "parent_id": "source:b", "title": "本B",
     "content": "無関係な話", "similarity": 0.23},
]


def test_evidence_floor_env(monkeypatch):
    monkeypatch.delenv("ASK_EVIDENCE_FLOOR", raising=False)
    assert ask_evidence_floor() == 0.4
    monkeypatch.setenv("ASK_EVIDENCE_FLOOR", "0.55")
    assert ask_evidence_floor() == 0.55
    monkeypatch.setenv("ASK_EVIDENCE_FLOOR", "junk")
    assert ask_evidence_floor() == 0.4


def test_top_similarity():
    assert top_similarity(HIGH_HITS) == 0.72
    assert top_similarity([]) == 0.0
    assert top_similarity([{"similarity": None}]) == 0.0


@pytest.mark.asyncio
async def test_low_similarity_refuses_without_calling_llm():
    with (
        patch(
            "open_notebook.graphs.ask.vector_search",
            new=AsyncMock(return_value=LOW_HITS),
        ),
        patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(),
        ) as mock_provision,
        patch(
            "open_notebook.graphs.ask.log_quality_event", new=AsyncMock()
        ) as mock_log,
    ):
        result = await provide_answer(
            {"question": "q", "term": "量子コンピュータ", "instructions": "i"},  # type: ignore[typeddict-item]
            {"configurable": {}},
        )

    assert len(result["answers"]) == 1
    assert result["answers"][0].startswith(REFUSAL_PREFIX)
    assert "量子コンピュータ" in result["answers"][0]
    mock_provision.assert_not_awaited()  # 回答LLMは呼ばれない
    assert mock_log.await_args.kwargs["kind"] == "ask_refusal"
    assert mock_log.await_args.kwargs["score"] == 0.23


@pytest.mark.asyncio
async def test_zero_hits_refuses():
    with (
        patch(
            "open_notebook.graphs.ask.vector_search", new=AsyncMock(return_value=[])
        ),
        patch(
            "open_notebook.graphs.ask.provision_langchain_model", new=AsyncMock()
        ) as mock_provision,
        patch("open_notebook.graphs.ask.log_quality_event", new=AsyncMock()),
    ):
        result = await provide_answer(
            {"question": "q", "term": "t", "instructions": "i"},  # type: ignore[typeddict-item]
            {"configurable": {}},
        )
    assert result["answers"][0].startswith(REFUSAL_PREFIX)
    mock_provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_high_similarity_answers_normally():
    model = AsyncMock()
    model.ainvoke = AsyncMock(return_value=type("M", (), {"content": "回答です"})())
    with (
        patch(
            "open_notebook.graphs.ask.vector_search",
            new=AsyncMock(return_value=HIGH_HITS),
        ),
        patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(return_value=model),
        ),
        patch("open_notebook.graphs.ask.log_quality_event", new=AsyncMock()) as mock_log,
    ):
        result = await provide_answer(
            {"question": "仮説思考とは", "term": "仮説思考", "instructions": "定義を"},  # type: ignore[typeddict-item]
            {"configurable": {}},
        )
    assert result["answers"] == ["回答です"]
    mock_log.assert_not_awaited()  # 通常経路はイベントなし


@pytest.mark.asyncio
async def test_final_answer_short_circuits_when_all_refused():
    with patch(
        "open_notebook.graphs.ask.provision_langchain_model", new=AsyncMock()
    ) as mock_provision:
        result = await write_final_answer(
            {  # type: ignore[typeddict-item]
                "question": "q",
                "answers": [f"{REFUSAL_PREFIX}根拠なし1", f"{REFUSAL_PREFIX}根拠なし2"],
            },
            {"configurable": {}},
        )
    assert result["final_answer"] == NO_EVIDENCE_ANSWER
    mock_provision.assert_not_awaited()  # 統合LLMも呼ばない


@pytest.mark.asyncio
async def test_final_answer_runs_llm_when_any_search_succeeded():
    model = AsyncMock()
    model.ainvoke = AsyncMock(return_value=type("M", (), {"content": "統合回答"})())
    with patch(
        "open_notebook.graphs.ask.provision_langchain_model",
        new=AsyncMock(return_value=model),
    ):
        result = await write_final_answer(
            {  # type: ignore[typeddict-item]
                "question": "q",
                "answers": [f"{REFUSAL_PREFIX}根拠なし", "実のある回答"],
            },
            {"configurable": {}},
        )
    assert result["final_answer"] == "統合回答"
