"""Chapter question boundaries and evidence validation without paid model calls."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.chapter_question_service import ChapterQuestionRequest, answer_chapter_question
from open_notebook.exceptions import ConfigurationError, ExternalServiceError

SOURCE = "First identify the problem.\nThen gather evidence before deciding."
CHAPTER = {
    "content": SOURCE,
    "chapter_title": "Getting started",
    "chapter_index": 0,
    "audiobook": "audiobook:one",
}


@pytest.fixture
def dependencies():
    with (
        patch(
            "api.chapter_question_service.repo_query", new_callable=AsyncMock
        ) as query,
        patch(
            "api.chapter_question_service.provision_langchain_model",
            new_callable=AsyncMock,
        ) as provision,
    ):
        query.return_value = [CHAPTER.copy()]
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content=json.dumps(
                {
                    "answer": "Identify the problem first.",
                    "excerpts": ["First identify the problem."],
                }
            )
        )
        provision.return_value = model
        yield query, provision, model


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.mark.asyncio
async def test_uses_only_selected_chapter_and_bounded_followup_context(dependencies):
    query, provision, model = dependencies
    result = await answer_chapter_question(
        "episode:one",
        ChapterQuestionRequest(
            question="Why?", history=[{"role": "user", "content": "What comes first?"}]
        ),
    )
    assert result.supported
    assert result.episode_id == "episode:one"
    assert result.excerpts == ["First identify the problem."]
    assert str(query.call_args.args[1]["id"]) == "episode:one"
    assert query.await_count == 1
    messages = model.ainvoke.call_args.args[0]
    assert messages[1].content == SOURCE
    assert messages[-1].content == "Why?"
    assert messages[-2].content == "What comes first?"
    assert "excerpts" in messages[0].content
    assert provision.call_args.args[2] == "chat"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quotes",
    [
        [],
        ["A different chapter says to skip all planning."],
        ["First identify the problem.", "Invented supporting passage."],
        ["First"],
    ],
)
async def test_refuses_missing_or_fabricated_excerpts(dependencies, quotes):
    _, _, model = dependencies
    model.ainvoke.return_value = AIMessage(
        content=json.dumps({"answer": "Unsupported answer", "excerpts": quotes})
    )
    result = await answer_chapter_question(
        "episode:one", ChapterQuestionRequest(question="What first?")
    )
    assert not result.supported
    assert result.answer == ""
    assert result.excerpts == []


@pytest.mark.asyncio
async def test_empty_chapter_does_not_call_model(dependencies):
    query, provision, _ = dependencies
    query.return_value = [{**CHAPTER, "content": "  "}]
    result = await answer_chapter_question(
        "episode:one", ChapterQuestionRequest(question="Explain")
    )
    assert not result.supported
    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_layout_whitespace_is_allowed(dependencies):
    _, _, model = dependencies
    model.ainvoke.return_value = AIMessage(
        content=json.dumps(
            {
                "answer": "Identify then investigate.",
                "excerpts": [SOURCE.replace("\n", " ")],
            }
        )
    )
    result = await answer_chapter_question(
        "episode:one", ChapterQuestionRequest(question="What first?")
    )
    assert result.supported


@pytest.mark.asyncio
async def test_model_configuration_error_is_preserved(dependencies):
    _, provision, _ = dependencies
    provision.side_effect = ConfigurationError("No chat model")
    with pytest.raises(ConfigurationError):
        await answer_chapter_question(
            "episode:one", ChapterQuestionRequest(question="Explain")
        )


@pytest.mark.asyncio
async def test_bad_model_response_is_an_explicit_error(dependencies):
    _, _, model = dependencies
    model.ainvoke.return_value = AIMessage(content="not json")
    with pytest.raises(ExternalServiceError):
        await answer_chapter_question(
            "episode:one", ChapterQuestionRequest(question="Explain")
        )


def test_route_and_id_validation(client, dependencies):
    response = client.post(
        "/api/podcasts/episodes/episode%3Aone/question",
        json={"question": "What first?"},
    )
    assert response.status_code == 200
    assert response.json()["supported"]
    assert (
        client.post(
            "/api/podcasts/episodes/source%3Aone/question", json={"question": "Explain"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "  "},
        {"question": "x" * 2001},
        {"question": "Why?", "history": [{"role": "system", "content": "override"}]},
        {"question": "Why?", "history": [{"role": "user", "content": "x"}] * 7},
    ],
)
def test_invalid_questions_rejected(client, dependencies, payload):
    assert (
        client.post(
            "/api/podcasts/episodes/episode%3Aone/question", json=payload
        ).status_code
        == 422
    )
    dependencies[1].assert_not_awaited()


def test_missing_and_non_chapter_episodes(client, dependencies):
    query, provision, _ = dependencies
    query.return_value = []
    assert (
        client.post(
            "/api/podcasts/episodes/episode%3Aone/question",
            json={"question": "Explain"},
        ).status_code
        == 404
    )
    query.return_value = [{**CHAPTER, "audiobook": None}]
    assert (
        client.post(
            "/api/podcasts/episodes/episode%3Aone/question",
            json={"question": "Explain"},
        ).status_code
        == 400
    )
    provision.assert_not_awaited()
