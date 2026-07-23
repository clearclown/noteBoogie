from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client after environment variables have been cleared by conftest."""
    from api.main import app

    return TestClient(app)


class TestSearchLimitValidation:
    """SearchRequest.limit must reject non-positive values (#863)."""

    @pytest.mark.parametrize("bad_limit", [0, -1, -100])
    def test_non_positive_limit_returns_422(self, bad_limit, client):
        response = client.post(
            "/api/search",
            json={"query": "x", "type": "text", "limit": bad_limit},
        )
        assert response.status_code == 422

    def test_limit_above_max_returns_422(self, client):
        response = client.post(
            "/api/search",
            json={"query": "x", "type": "text", "limit": 1001},
        )
        assert response.status_code == 422

    @patch("api.routers.search.text_search", new_callable=AsyncMock)
    def test_valid_limit_returns_200(self, mock_text_search, client):
        mock_text_search.return_value = []
        response = client.post(
            "/api/search",
            json={"query": "x", "type": "text", "limit": 10},
        )
        assert response.status_code == 200
        mock_text_search.assert_awaited_once()


class TestTextSearchHighlightOverflowFallback:
    """text_search() must fall back to vector search on a highlight position overflow (#648)."""

    @pytest.mark.asyncio
    async def test_position_overflow_falls_back_to_vector_search(self):
        from open_notebook.domain import notebook as notebook_module

        overflow = RuntimeError(
            "A value can't be highlighted: position overflow: 2545 - len: 1965"
        )
        with (
            patch.object(
                notebook_module, "repo_query", new_callable=AsyncMock, side_effect=overflow
            ),
            patch.object(
                notebook_module,
                "vector_search",
                new_callable=AsyncMock,
                return_value=[{"id": "source:1"}],
            ) as mock_vector,
        ):
            result = await notebook_module.text_search("hello", 10)

        assert result == [{"id": "source:1"}]
        mock_vector.assert_awaited_once_with("hello", 10, True, True)

    @pytest.mark.asyncio
    async def test_position_overflow_raises_when_vector_also_fails(self):
        from open_notebook.domain import notebook as notebook_module
        from open_notebook.exceptions import DatabaseOperationError

        overflow = RuntimeError("position overflow: 1 - len: 0")
        with (
            patch.object(
                notebook_module, "repo_query", new_callable=AsyncMock, side_effect=overflow
            ),
            patch.object(
                notebook_module,
                "vector_search",
                new_callable=AsyncMock,
                side_effect=Exception("no embedding model"),
            ),
        ):
            # When both search paths fail, surface the error rather than masking it
            # as an empty result set.
            with pytest.raises(DatabaseOperationError):
                await notebook_module.text_search("hello", 10)

    @pytest.mark.asyncio
    async def test_other_runtime_errors_still_raise(self):
        from open_notebook.domain import notebook as notebook_module
        from open_notebook.exceptions import DatabaseOperationError

        with patch.object(
            notebook_module,
            "repo_query",
            new_callable=AsyncMock,
            side_effect=RuntimeError("some other db failure"),
        ):
            with pytest.raises(DatabaseOperationError):
                await notebook_module.text_search("hello", 10)


# ---------------------------------------------------------------------------
# Ask endpoints (notebook_id passthrough — Book Navigator)
# ---------------------------------------------------------------------------


def _three_models():
    model = MagicMock()
    model.id = "model:m1"
    return model


class TestAskEndpoints:
    @pytest.mark.asyncio
    @patch("api.routers.search.model_manager")
    @patch("api.routers.search.Model")
    async def test_ask_simple_threads_notebook_id_into_graph_config(
        self, mock_model, mock_manager, client
    ):
        mock_model.get = AsyncMock(return_value=_three_models())
        mock_manager.get_embedding_model = AsyncMock(return_value=MagicMock())
        captured = {}

        def fake_astream(input=None, config=None, stream_mode=None):
            captured["input"] = input
            captured["config"] = config

            async def gen():
                yield {"write_final_answer": {"final_answer": "回答"}}

            return gen()

        with patch("api.routers.search.ask_graph") as mock_graph:
            mock_graph.astream = fake_astream
            response = client.post(
                "/api/search/ask/simple",
                json={
                    "question": "仮説思考とは",
                    "strategy_model": "model:m1",
                    "answer_model": "model:m1",
                    "final_answer_model": "model:m1",
                    "notebook_id": "notebook:x",
                },
            )

        assert response.status_code == 200
        assert response.json()["answer"] == "回答"
        assert captured["config"]["configurable"]["notebook_id"] == "notebook:x"

    @pytest.mark.asyncio
    @patch("api.routers.search.model_manager")
    @patch("api.routers.search.Model")
    async def test_ask_simple_defaults_to_global_scope(
        self, mock_model, mock_manager, client
    ):
        mock_model.get = AsyncMock(return_value=_three_models())
        mock_manager.get_embedding_model = AsyncMock(return_value=MagicMock())
        captured = {}

        def fake_astream(input=None, config=None, stream_mode=None):
            captured["config"] = config

            async def gen():
                yield {"write_final_answer": {"final_answer": "回答"}}

            return gen()

        with patch("api.routers.search.ask_graph") as mock_graph:
            mock_graph.astream = fake_astream
            response = client.post(
                "/api/search/ask/simple",
                json={
                    "question": "q",
                    "strategy_model": "model:m1",
                    "answer_model": "model:m1",
                    "final_answer_model": "model:m1",
                },
            )
        assert response.status_code == 200
        assert captured["config"]["configurable"]["notebook_id"] is None

    @pytest.mark.asyncio
    @patch("api.routers.search.model_manager")
    @patch("api.routers.search.Model")
    async def test_ask_streaming_endpoint_returns_sse(
        self, mock_model, mock_manager, client
    ):
        mock_model.get = AsyncMock(return_value=_three_models())
        mock_manager.get_embedding_model = AsyncMock(return_value=MagicMock())

        def fake_astream(input=None, config=None, stream_mode=None):
            async def gen():
                yield {"write_final_answer": {"final_answer": "回答"}}

            return gen()

        with patch("api.routers.search.ask_graph") as mock_graph:
            mock_graph.astream = fake_astream
            response = client.post(
                "/api/search/ask",
                json={
                    "question": "q",
                    "strategy_model": "model:m1",
                    "answer_model": "model:m1",
                    "final_answer_model": "model:m1",
                    "notebook_id": "notebook:x",
                },
            )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "final_answer" in response.text
