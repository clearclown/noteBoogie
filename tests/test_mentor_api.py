"""Tests for the mentor UI API (api/routers/mentor.py + api/mentor_service.py)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.mentor_service import (
    audio_cache_path,
    extract_source_refs,
    strip_markdown_for_speech,
)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


# --- unit: helpers ----------------------------------------------------------


def test_extract_source_refs_dedupes_and_keeps_order():
    refs = extract_source_refs(
        [
            {"parent_id": "source:a", "title": "本A"},
            {"parent_id": "source:b", "title": "本B"},
            {"parent_id": "source:a", "title": "本A"},
            {"id": "source:c"},
        ]
    )
    assert [(r.id, r.title) for r in refs] == [
        ("source:a", "本A"),
        ("source:b", "本B"),
        ("source:c", "source:c"),
    ]


def test_strip_markdown_for_speech_removes_decoration():
    text = "# 見出し\n**結論**から言うと、`code` は[リンク](http://x)です。\n- 箇条書き\n1. 番号"
    out = strip_markdown_for_speech(text)
    assert "#" not in out and "**" not in out and "`" not in out
    assert "結論から言うと、code はリンクです。" in out
    assert "箇条書き" in out and "- " not in out
    assert "番号" in out and "1. " not in out


def test_strip_markdown_for_speech_drops_code_blocks():
    assert strip_markdown_for_speech("前\n```python\nprint(1)\n```\n後") == "前\n\n後"


def test_audio_cache_path_uses_record_key_only():
    path = audio_cache_path("mentor_message:abc123")
    assert path.name == "abc123.mp3"


def test_audio_cache_path_rejects_traversal():
    from open_notebook.exceptions import InvalidInputError

    path = audio_cache_path("mentor_message:../../etc/passwd")
    # 危険文字は全て除去され、キャッシュディレクトリ直下に収まる
    assert path.name == "etcpasswd.mp3"
    with pytest.raises(InvalidInputError):
        audio_cache_path("mentor_message:../..")


# --- consult ----------------------------------------------------------------


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_create", new_callable=AsyncMock)
async def test_consult_returns_answer_sources_and_logs(mock_create, client):
    mock_create.side_effect = [
        {"id": "mentor_message:u1"},
        {"id": "mentor_message:m1"},
    ]
    fake_result = {
        "answer": "結論から言うと…",
        "search_results": [
            {"parent_id": "source:a", "title": "コンサル頭のつくり方"},
        ],
    }
    with patch(
        "open_notebook.graphs.mentor.graph.ainvoke",
        new=AsyncMock(return_value=fake_result),
    ):
        response = client.post("/api/mentor/consult", json={"message": "壁打ちしたい"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "結論から言うと…"
    assert body["sources"] == [{"id": "source:a", "title": "コンサル頭のつくり方"}]
    assert body["message_id"] == "mentor_message:m1"
    # user 行と mentor 行の2行が書かれる
    assert mock_create.await_count == 2
    roles = [call.args[1]["role"] for call in mock_create.await_args_list]
    assert roles == ["user", "mentor"]


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_create", new_callable=AsyncMock)
async def test_consult_survives_log_failure(mock_create, client):
    mock_create.side_effect = RuntimeError("db down")
    with patch(
        "open_notebook.graphs.mentor.graph.ainvoke",
        new=AsyncMock(return_value={"answer": "回答", "search_results": []}),
    ):
        response = client.post("/api/mentor/consult", json={"message": "相談"})
    assert response.status_code == 200
    assert response.json() == {"answer": "回答", "sources": [], "message_id": None}


def test_consult_rejects_empty_message(client):
    assert client.post("/api/mentor/consult", json={"message": ""}).status_code == 422


# --- speak ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_speak_synthesizes_and_caches(mock_query, client, tmp_path, monkeypatch):
    import api.mentor_service as svc

    monkeypatch.setattr(svc, "MENTOR_AUDIO_DIR", tmp_path)
    mock_query.return_value = [{"content": "**結論**です", "role": "mentor"}]
    tts = SimpleNamespace(
        agenerate_speech=AsyncMock(return_value=SimpleNamespace(content=b"mp3bytes"))
    )
    with patch(
        "open_notebook.ai.models.model_manager.get_text_to_speech",
        new=AsyncMock(return_value=tts),
    ):
        response = client.post("/api/mentor/speak/mentor_message%3Am1")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"mp3bytes"
    # markdown 装飾は読み上げテキストから落ちる
    assert tts.agenerate_speech.await_args.kwargs["text"] == "結論です"
    assert (tmp_path / "m1.mp3").read_bytes() == b"mp3bytes"


@pytest.mark.asyncio
async def test_speak_serves_from_cache_without_tts(client, tmp_path, monkeypatch):
    import api.mentor_service as svc

    monkeypatch.setattr(svc, "MENTOR_AUDIO_DIR", tmp_path)
    (tmp_path / "cached.mp3").write_bytes(b"cached-audio")
    response = client.post("/api/mentor/speak/mentor_message%3Acached")
    assert response.status_code == 200
    assert response.content == b"cached-audio"


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_speak_404_when_message_missing(mock_query, client, tmp_path, monkeypatch):
    import api.mentor_service as svc

    monkeypatch.setattr(svc, "MENTOR_AUDIO_DIR", tmp_path)
    mock_query.return_value = []
    assert client.post("/api/mentor/speak/mentor_message%3Anope").status_code == 404


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_speak_422_when_no_tts_model(mock_query, client, tmp_path, monkeypatch):
    import api.mentor_service as svc

    monkeypatch.setattr(svc, "MENTOR_AUDIO_DIR", tmp_path)
    mock_query.return_value = [{"content": "回答", "role": "mentor"}]
    with patch(
        "open_notebook.ai.models.model_manager.get_text_to_speech",
        new=AsyncMock(return_value=None),
    ):
        response = client.post("/api/mentor/speak/mentor_message%3Am2")
    assert response.status_code == 422


# --- messages / memories ----------------------------------------------------


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_messages_returned_oldest_first(mock_query, client):
    mock_query.return_value = [
        {"id": "mentor_message:2", "role": "mentor", "content": "回答", "sources": None, "created": "t2"},
        {"id": "mentor_message:1", "role": "user", "content": "質問", "sources": None, "created": "t1"},
    ]
    response = client.get("/api/mentor/messages")
    assert response.status_code == 200
    assert [m["id"] for m in response.json()] == ["mentor_message:1", "mentor_message:2"]


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_memories_listed(mock_query, client):
    mock_query.return_value = [
        {"id": "mentor_memory:1", "question": "q", "gist": "g", "sources": ["source:a"], "created": "t"}
    ]
    response = client.get("/api/mentor/memories")
    assert response.status_code == 200
    assert response.json()[0]["question"] == "q"


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_delete", new_callable=AsyncMock)
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_delete_memory(mock_query, mock_delete, client):
    mock_query.return_value = [{"id": "mentor_memory:x"}]
    response = client.delete("/api/mentor/memories/mentor_memory%3Ax")
    assert response.status_code == 200
    mock_delete.assert_awaited_once_with("mentor_memory:x")


def test_delete_memory_rejects_foreign_table(client):
    assert client.delete("/api/mentor/memories/source%3Aa").status_code == 400


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_delete_memory_404(mock_query, client):
    mock_query.return_value = []
    assert client.delete("/api/mentor/memories/mentor_memory%3Anope").status_code == 404


# --- weights ----------------------------------------------------------------


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_get_weights_merges_manual_auto_and_chapters(mock_query, client):
    def responses(query, params=None):
        if query.startswith("SELECT type::string(id) AS id, title FROM source"):
            return [
                {"id": "source:a", "title": "本A"},
                {"id": "source:b", "title": "本B"},
            ]
        if "FROM mentor_source_weight" in query:
            return [
                {"source_id": "source:a", "weight": 1.5, "chapter_weights": {"0": 2.0}}
            ]
        if "FROM mentor_memory" in query:
            return [{"sources": ["source:a"]}, {"sources": ["source:a"]}]
        if "FROM episode" in query:
            return [
                {"chapter_index": 0, "chapter_title": "第1章", "source_id": "source:a"},
                {"chapter_index": 1, "chapter_title": "第2章", "source_id": "source:a"},
            ]
        raise AssertionError(f"unexpected query: {query}")

    mock_query.side_effect = responses
    response = client.get("/api/mentor/weights")
    assert response.status_code == 200
    a, b = response.json()
    assert a["source_id"] == "source:a"
    assert a["weight"] == 1.5
    assert a["chapter_weights"] == {"0": 2.0}
    assert a["auto_factor"] > 1.0
    assert a["chapters"] == ["第1章", "第2章"]
    assert b == {
        "source_id": "source:b",
        "title": "本B",
        "weight": 1.0,
        "chapter_weights": None,
        "auto_factor": 1.0,
        "chapters": [],
    }


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_put_weight_upserts(mock_query, client):
    mock_query.side_effect = [
        [{"id": "source:a", "title": "本A"}],  # existence check
        [],  # upsert
    ]
    response = client.put(
        "/api/mentor/weights/source%3Aa",
        json={"weight": 0.5, "chapter_weights": {"2": 1.5}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["weight"] == 0.5 and body["chapter_weights"] == {"2": 1.5}
    upsert_call = mock_query.await_args_list[1]
    assert "UPSERT mentor_source_weight" in upsert_call.args[0]


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_put_weight_404_for_unknown_source(mock_query, client):
    mock_query.return_value = []
    response = client.put("/api/mentor/weights/source%3Anope", json={"weight": 1.0})
    assert response.status_code == 404


def test_put_weight_validates_range(client):
    assert (
        client.put("/api/mentor/weights/source%3Aa", json={"weight": 3.0}).status_code
        == 422
    )


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_put_weight_validates_chapter_range(mock_query, client):
    mock_query.return_value = [{"id": "source:a", "title": "本A"}]
    response = client.put(
        "/api/mentor/weights/source%3Aa",
        json={"weight": 1.0, "chapter_weights": {"0": 9.9}},
    )
    assert response.status_code == 400


def test_put_weight_rejects_foreign_table(client):
    assert (
        client.put("/api/mentor/weights/note%3Aa", json={"weight": 1.0}).status_code
        == 400
    )


# --- persona (汎用化: コンサル既定 + プリセット切替) --------------------------


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_get_persona_falls_back_to_domain_neutral_default(mock_query, client):
    mock_query.return_value = []
    response = client.get("/api/mentor/persona")
    assert response.status_code == 200
    body = response.json()
    assert body["is_default"] is True
    # コード側フォールバックはドメイン非依存（特定職種に固定しない）
    assert "コンサルタント" not in body["persona"]
    assert "師匠" in body["persona"]


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_get_persona_returns_active_profile(mock_query, client):
    mock_query.return_value = [{"persona": "あなたは経験豊富な外科医の師匠です。"}]
    body = client.get("/api/mentor/persona").json()
    assert body == {"persona": "あなたは経験豊富な外科医の師匠です。", "is_default": False}
    # active = true の行を読む
    assert "active = true" in mock_query.await_args_list[0].args[0]


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_list_personas_orders_active_then_default(mock_query, client):
    mock_query.return_value = [
        {"name": "engineer", "persona": "エンジニアの師匠", "active": False},
        {"name": "default", "persona": "コンサルの師匠", "active": False},
        {"name": "editor", "persona": "編集長の師匠", "active": True},
    ]
    body = client.get("/api/mentor/personas").json()
    assert [p["name"] for p in body] == ["editor", "default", "engineer"]
    assert body[0]["active"] is True


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_upsert_persona_profile(mock_query, client):
    mock_query.side_effect = [[], [{"active": False}]]
    response = client.put(
        "/api/mentor/personas/chef",
        json={"persona": "あなたは経験豊富な料理長の師匠です。弟子の腕を引き上げます。"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "chef" and body["active"] is False
    assert "UPSERT mentor_profile" in mock_query.await_args_list[0].args[0]


def test_upsert_persona_rejects_bad_name(client):
    response = client.put(
        "/api/mentor/personas/Bad%20Name!",
        json={"persona": "あなたは経験豊富な師匠です。弟子を導きます。"},
    )
    assert response.status_code == 400


def test_upsert_persona_validates_length(client):
    assert (
        client.put("/api/mentor/personas/chef", json={"persona": "短い"}).status_code
        == 422
    )


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_activate_persona_switches_single_active(mock_query, client):
    mock_query.side_effect = [
        [{"name": "engineer", "persona": "エンジニアの師匠"}],
        [],
    ]
    response = client.post("/api/mentor/personas/engineer/activate")
    assert response.status_code == 200
    assert response.json()["active"] is True
    # 1クエリで「選択行のみ true、他は false」に揃える
    update_query = mock_query.await_args_list[1].args[0]
    assert "SET active = (name = $name)" in update_query


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_activate_unknown_persona_404(mock_query, client):
    mock_query.return_value = []
    assert client.post("/api/mentor/personas/nope/activate").status_code == 404
