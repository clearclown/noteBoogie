"""Unit tests for scripts/setup_book_navigator_models.py (repo_query mocked)."""

from unittest.mock import AsyncMock

import pytest

import scripts.setup_book_navigator_models as setup


def make_query_mock(existing_ids=None):
    """repo_query stub: SELECT returns `existing_ids`, CREATE mints a new id."""
    calls = []

    async def fake_query(q, binds=None):
        calls.append((q, binds))
        if q.startswith("SELECT"):
            return [{"id": i} for i in (existing_ids or [])]
        if q.startswith("CREATE"):
            return [{"id": "model:new123"}]
        return []

    return fake_query, calls


class TestUpsertModel:
    @pytest.mark.asyncio
    async def test_reuses_existing_model(self, monkeypatch):
        fake, calls = make_query_mock(existing_ids=["model:old"])
        monkeypatch.setattr(setup, "repo_query", fake)
        assert await setup._upsert_model("m", "anthropic", "language") == "model:old"
        assert len(calls) == 1, "no CREATE when the model exists"

    @pytest.mark.asyncio
    async def test_creates_when_missing(self, monkeypatch):
        fake, calls = make_query_mock()
        monkeypatch.setattr(setup, "repo_query", fake)
        assert await setup._upsert_model("m", "anthropic", "language") == "model:new123"
        create_q, binds = calls[1]
        assert create_q.startswith("CREATE model")
        assert binds == {"n": "m", "p": "anthropic", "t": "language"}


class TestMain:
    @pytest.mark.asyncio
    async def test_links_profiles_and_skips_tts_when_empty(self, monkeypatch):
        fake, calls = make_query_mock()
        monkeypatch.setattr(setup, "repo_query", fake)
        await setup.main("anthropic", "claude-sonnet-5", "", "google")

        updates = [q for q, _ in calls if q.startswith("UPDATE")]
        assert any("episode_profile" in q and "outline_llm" in q for q in updates)
        # Empty tts model -> the speaker profile is never touched.
        assert not any("speaker_profile" in q for q in updates)

    @pytest.mark.asyncio
    async def test_links_voice_model_with_separate_tts_provider(self, monkeypatch):
        fake, calls = make_query_mock()
        monkeypatch.setattr(setup, "repo_query", fake)
        await setup.main("anthropic", "claude-sonnet-5", "gemini-tts", "google")

        assert any(
            q.startswith("UPDATE speaker_profile") and b == {"tid": "new123"}
            for q, b in calls
        )
        # The TTS model row is created under the TTS provider, not the LLM one.
        tts_create = [b for q, b in calls if q.startswith("CREATE") and b["t"] == "text_to_speech"]
        assert tts_create[0]["p"] == "google"


class TestSetDefaults:
    @pytest.mark.asyncio
    async def test_upserts_all_default_slots(self, monkeypatch):
        fake, calls = make_query_mock()
        monkeypatch.setattr(setup, "repo_query", fake)
        await setup.set_defaults(
            "anthropic", "claude-sonnet-5", "google", "gemini-embedding-001", "model:tts9"
        )
        upsert_q, binds = calls[-1]
        assert upsert_q.startswith("UPSERT open_notebook:default_models")
        for slot in (
            "default_chat_model",
            "default_transformation_model",
            "default_tools_model",
            "large_context_model",
            "default_embedding_model",
            "default_text_to_speech_model",
        ):
            assert slot in upsert_q, slot
        assert binds["tid"] == "tts9"

    @pytest.mark.asyncio
    async def test_tts_slot_omitted_without_tts_model(self, monkeypatch):
        fake, calls = make_query_mock()
        monkeypatch.setattr(setup, "repo_query", fake)
        await setup.set_defaults(
            "anthropic", "claude-sonnet-5", "google", "gemini-embedding-001", None
        )
        upsert_q, binds = calls[-1]
        assert "default_text_to_speech_model" not in upsert_q
        assert "tid" not in binds
