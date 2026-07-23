"""Tests for the Python gRPC podcast sidecar.

The gRPC dependency lives in the optional `sidecar` group; tests that need it
are skipped when grpcio is not installed (`uv run pytest` without --group sidecar).
"""

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_runner_module_exposes_api():
    """The runner wraps podcast-creator and exposes the expected entry points."""
    from sidecar import podcast_runner

    assert hasattr(podcast_runner, "run_create_podcast")
    assert hasattr(podcast_runner, "_configure_podcast_creator")
    assert hasattr(podcast_runner, "CreatePodcastResult")


@pytest.mark.asyncio
async def test_ping_roundtrip():
    """Start the sidecar gRPC server on an ephemeral port and Ping it."""
    grpc = pytest.importorskip("grpc")

    gen_dir = REPO_ROOT / "sidecar" / "gen"
    if str(gen_dir) not in sys.path:
        sys.path.insert(0, str(gen_dir))
    pytest.importorskip("podcast_pb2")

    import podcast_pb2
    import podcast_pb2_grpc

    from sidecar.podcast_sidecar import PodcastSidecarServicer

    server = grpc.aio.server()
    podcast_pb2_grpc.add_PodcastSidecarServicer_to_server(
        PodcastSidecarServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = podcast_pb2_grpc.PodcastSidecarStub(channel)
            resp = await asyncio.wait_for(stub.Ping(podcast_pb2.PingRequest()), timeout=5)
            assert resp.ok is True
            assert resp.version
    finally:
        await server.stop(0)


# ---------------------------------------------------------------------------
# podcast_runner unit tests (no grpc needed — runner is import-light)
# ---------------------------------------------------------------------------


def test_to_jsonable_recurses_models_dicts_lists():
    from pydantic import BaseModel

    from sidecar.podcast_runner import _to_jsonable

    class Dialogue(BaseModel):
        speaker: str
        dialogue: str

    nested = {
        "transcript": [Dialogue(speaker="Mentor", dialogue="こんにちは")],
        "meta": {"n": 1, "inner": [Dialogue(speaker="M", dialogue="x")]},
        "scalar": "s",
    }
    out = _to_jsonable(nested)
    assert out["transcript"][0] == {"speaker": "Mentor", "dialogue": "こんにちは"}
    assert out["meta"]["inner"][0]["dialogue"] == "x"
    assert out["scalar"] == "s"
    # Result must be plain-JSON serializable end to end.
    import json

    json.dumps(out)


@pytest.mark.asyncio
async def test_configure_drops_unresolvable_profiles(monkeypatch):
    from unittest.mock import MagicMock

    from sidecar import podcast_runner as pr

    episode_profiles = [
        {"name": "good", "outline_llm": "model:ok", "transcript_llm": "model:ok"},
        {"name": "bad", "outline_llm": "model:broken"},
        {"name": "no_models"},
    ]
    speaker_profiles = [
        {"name": "voiced", "voice_model": "model:tts", "speakers": []},
        {"name": "broken_voice", "voice_model": "model:broken", "speakers": []},
        {
            "name": "per_speaker",
            "speakers": [{"name": "A", "voice_model": "model:broken"}],
        },
    ]

    async def fake_repo_query(q, *a, **kw):
        return episode_profiles if "episode_profile" in q else speaker_profiles

    async def fake_resolve(model_id):
        if "broken" in model_id:
            raise ValueError("no such model")
        return ("google", "gemini-x", {"api_key": "k"})

    monkeypatch.setattr(pr, "repo_query", fake_repo_query)
    monkeypatch.setattr(pr, "_resolve_model_config", fake_resolve)
    configure = MagicMock()
    monkeypatch.setattr(pr, "configure", configure)

    await pr._configure_podcast_creator()

    calls = {c.args[0]: c.args[1] for c in configure.call_args_list}
    episode_names = set(calls["episode_config"]["profiles"])
    speaker_names = set(calls["speakers_config"]["profiles"])
    # Unresolvable profile-level models drop the profile...
    assert episode_names == {"good", "no_models"}
    assert speaker_names == {"voiced", "per_speaker"}
    # ...but a per-speaker failure only logs; the profile survives.
    good = calls["episode_config"]["profiles"]["good"]
    assert good["outline_provider"] == "google"
    assert good["transcript_model"] == "gemini-x"


@pytest.mark.asyncio
async def test_run_create_podcast_handles_none_result(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from sidecar import podcast_runner as pr

    monkeypatch.setattr(pr, "_configure_podcast_creator", AsyncMock())
    monkeypatch.setattr(pr, "create_podcast", AsyncMock(return_value=None))
    out_dir = tmp_path / "new" / "episode"
    result = await pr.run_create_podcast(
        content="c",
        briefing="b",
        episode_name="e",
        output_dir=str(out_dir),
        speaker_config="sp",
        episode_profile="ep",
    )
    assert out_dir.is_dir(), "output dir is created up front"
    assert result.final_output_file_path is None
    assert result.transcript is None
    assert result.outline is None


@pytest.mark.asyncio
async def test_run_create_podcast_serializes_result(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from pydantic import BaseModel

    from sidecar import podcast_runner as pr

    class Seg(BaseModel):
        dialogue: str

    monkeypatch.setattr(pr, "_configure_podcast_creator", AsyncMock())
    monkeypatch.setattr(
        pr,
        "create_podcast",
        AsyncMock(
            return_value={
                "final_output_file_path": tmp_path / "a.mp3",
                "transcript": [Seg(dialogue="x")],
                "outline": {"segments": []},
            }
        ),
    )
    result = await pr.run_create_podcast(
        content="c",
        briefing="b",
        episode_name="e",
        output_dir=str(tmp_path),
        speaker_config="sp",
        episode_profile="ep",
    )
    assert result.final_output_file_path == str(tmp_path / "a.mp3")
    assert result.transcript == [{"dialogue": "x"}]
    assert result.outline == {"segments": []}


# ---------------------------------------------------------------------------
# Servicer error mapping (grpc required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_podcast_value_error_maps_to_invalid_argument(monkeypatch):
    grpc = pytest.importorskip("grpc")
    gen_dir = REPO_ROOT / "sidecar" / "gen"
    if str(gen_dir) not in sys.path:
        sys.path.insert(0, str(gen_dir))
    pytest.importorskip("podcast_pb2")

    from unittest.mock import AsyncMock, MagicMock

    import sidecar.podcast_sidecar as sc

    monkeypatch.setattr(
        sc, "run_create_podcast", AsyncMock(side_effect=ValueError("bad profile"))
    )

    class _Abort(Exception):
        pass

    context = MagicMock()
    context.abort = AsyncMock(side_effect=_Abort())
    request = MagicMock(
        content="c",
        briefing="b",
        episode_name="e",
        output_dir="/tmp/x",
        speaker_config="s",
        episode_profile="p",
    )

    with pytest.raises(_Abort):
        await sc.PodcastSidecarServicer().CreatePodcast(request, context)
    context.abort.assert_awaited_once()
    assert context.abort.call_args.args[0] == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_create_podcast_unexpected_error_maps_to_internal(monkeypatch):
    grpc = pytest.importorskip("grpc")
    gen_dir = REPO_ROOT / "sidecar" / "gen"
    if str(gen_dir) not in sys.path:
        sys.path.insert(0, str(gen_dir))
    pytest.importorskip("podcast_pb2")

    from unittest.mock import AsyncMock, MagicMock

    import sidecar.podcast_sidecar as sc

    monkeypatch.setattr(
        sc, "run_create_podcast", AsyncMock(side_effect=RuntimeError("db down"))
    )

    class _Abort(Exception):
        pass

    context = MagicMock()
    context.abort = AsyncMock(side_effect=_Abort())
    request = MagicMock(
        content="c",
        briefing="b",
        episode_name="e",
        output_dir="/tmp/x",
        speaker_config="s",
        episode_profile="p",
    )

    with pytest.raises(_Abort):
        await sc.PodcastSidecarServicer().CreatePodcast(request, context)
    assert context.abort.call_args.args[0] == grpc.StatusCode.INTERNAL
