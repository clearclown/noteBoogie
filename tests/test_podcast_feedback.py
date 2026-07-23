"""Tests for the chapter feedback endpoint (PUT /podcasts/episodes/{id}/feedback)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_set_feedback_up(mock_query, client):
    mock_query.side_effect = [[{"id": "episode:e1"}], []]
    response = client.put(
        "/api/podcasts/episodes/episode%3Ae1/feedback", json={"rating": "up"}
    )
    assert response.status_code == 200
    assert response.json() == {"id": "episode:e1", "feedback": "up"}
    update_call = mock_query.await_args_list[1]
    assert "UPDATE episode SET feedback" in update_call.args[0]
    assert update_call.args[1]["rating"] == "up"


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_clear_feedback_with_null(mock_query, client):
    mock_query.side_effect = [[{"id": "episode:e1"}], []]
    response = client.put(
        "/api/podcasts/episodes/episode%3Ae1/feedback", json={"rating": None}
    )
    assert response.status_code == 200
    assert response.json()["feedback"] is None


def test_invalid_rating_rejected(client):
    response = client.put(
        "/api/podcasts/episodes/episode%3Ae1/feedback", json={"rating": "meh"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_unknown_episode_404(mock_query, client):
    mock_query.return_value = []
    response = client.put(
        "/api/podcasts/episodes/episode%3Anope/feedback", json={"rating": "down"}
    )
    assert response.status_code == 404
