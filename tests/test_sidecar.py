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
    # Unresolvable profile-level models drop the profile; profiles that end
    # up WITHOUT resolved provider/model pairs are dropped too (strict
    # podcast-creator validation covers the whole dict).
    assert episode_names == {"good"}
    assert speaker_names == {"voiced"}
    # ...but a per-speaker failure only logs; the profile survives.
    good = calls["episode_config"]["profiles"]["good"]
    assert good["outline_provider"] == "google"
    assert good["transcript_model"] == "gemini-x"


@pytest.mark.asyncio
async def test_run_create_podcast_handles_none_result(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    # Result serialization is shared; pin the two-pass path explicitly.
    monkeypatch.setenv("SIDECAR_SINGLE_PASS", "0")

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

    # Result serialization is shared; pin the two-pass path explicitly.
    monkeypatch.setenv("SIDECAR_SINGLE_PASS", "0")

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


def test_sanitize_drops_unresolved_and_datetime_fields():
    import datetime

    from sidecar.podcast_runner import _sanitize_profiles

    episodes = {
        "resolved": {
            "transcript_provider": "anthropic",
            "transcript_model": "claude-sonnet-5",
            "created": datetime.datetime.now(),
        },
        "upstream_seed": {"name": "business_panel", "created": datetime.datetime.now()},
    }
    speakers = {
        "voiced": {"tts_provider": "google", "tts_model": "tts-x", "updated": datetime.datetime.now()},
        "unvoiced_seed": {"name": "tech_experts"},
    }
    _sanitize_profiles(episodes, speakers)
    assert set(episodes) == {"resolved"}
    assert set(speakers) == {"voiced"}
    assert "created" not in episodes["resolved"]
    assert "updated" not in speakers["voiced"]


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


# ---------------------------------------------------------------------------
# Single-pass (outline-skip) path
# ---------------------------------------------------------------------------


def test_synthetic_outline_encodes_the_three_part_structure():
    from sidecar.podcast_runner import build_synthetic_outline

    outline = build_synthetic_outline(3)
    names = [s.name for s in outline.segments]
    assert names == ["導入", "本編", "アクションプラン"]
    assert outline.segments[1].size == "long"
    # Fewer segments requested -> truncated, never empty.
    assert len(build_synthetic_outline(1).segments) == 1
    assert len(build_synthetic_outline(0).segments) == 1


def test_split_graphs_have_no_outline_node():
    from sidecar.podcast_runner import _build_audio_graph, _build_transcript_graph

    transcript_nodes = set(_build_transcript_graph().get_graph().nodes)
    audio_nodes = set(_build_audio_graph().get_graph().nodes)
    assert "generate_transcript" in transcript_nodes
    assert "generate_all_audio" not in transcript_nodes  # TTS はゲート通過後のみ
    assert {"generate_all_audio", "combine_audio"} <= audio_nodes
    assert "generate_outline" not in transcript_nodes | audio_nodes


# ---------------------------------------------------------------------------
# Transcript quality gate (score before TTS)
# ---------------------------------------------------------------------------

GOOD_TRANSCRIPT = (
    "この章は仮説思考が身につかないという悩みに答えます。"
    "1つ目は仮説思考です。仮説思考とは結論から考える技術です。"
    "2つ目は論点思考です。論点思考で問いを絞ります。"
    "3つ目はイシュー分析です。イシューを分解して検証します。"
    "最後にアクションプランです。明日から仮説思考を実践してみてください。"
) * 3

BAD_TRANSCRIPT = "量子コンピュータとブロックチェーンの話だ。以上。"

CHAPTER_CONTENT = (
    "仮説思考とは結論から考える技術である。論点思考は問いを絞る技術。"
    "イシュー分析はイシューを分解して検証する。実践が重要だ。"
) * 4


def test_gate_env_defaults(monkeypatch):
    from sidecar import podcast_runner as pr

    monkeypatch.delenv("SIDECAR_GATE", raising=False)
    monkeypatch.delenv("SIDECAR_GATE_THRESHOLD", raising=False)
    assert pr.gate_enabled() is True
    assert pr.gate_threshold() == 0.6
    monkeypatch.setenv("SIDECAR_GATE", "0")
    monkeypatch.setenv("SIDECAR_GATE_THRESHOLD", "0.8")
    assert pr.gate_enabled() is False
    assert pr.gate_threshold() == 0.8
    monkeypatch.setenv("SIDECAR_GATE_THRESHOLD", "garbage")
    assert pr.gate_threshold() == 0.6


def test_gate_decision_picks_best_attempt():
    from scripts.eval_transcript import evaluate_chapter
    from sidecar.podcast_runner import gate_decision

    bad = evaluate_chapter("ch", CHAPTER_CONTENT, BAD_TRANSCRIPT)
    good = evaluate_chapter("ch", CHAPTER_CONTENT, GOOD_TRANSCRIPT)
    index, passed = gate_decision([bad, good], threshold=0.6)
    assert index == 1 and passed is True
    index, passed = gate_decision([bad], threshold=0.6)
    assert index == 0 and passed is False


def test_gate_critique_names_the_failures():
    from scripts.eval_transcript import evaluate_chapter
    from sidecar.podcast_runner import build_gate_critique

    ev = evaluate_chapter("ch", CHAPTER_CONTENT, BAD_TRANSCRIPT)
    critique = build_gate_critique(ev, threshold=0.6)
    assert "品質レビュー指摘" in critique
    assert "1つ目" in critique  # structure guidance
    assert "章本文に存在しない" in critique  # grounding guidance
    assert "です/ます" in critique  # politeness guidance


def _gate_test_setup(monkeypatch, tmp_path, transcripts):
    """create_podcast_single_pass をグラフ・profile 読込をモックして駆動する。

    transcripts: transcript グラフ呼び出しごとに返す台本のリスト。
    戻り値: (podcast_runner, transcript_mock, audio_mock, logged_events)
    """
    from unittest.mock import AsyncMock, MagicMock

    from sidecar import podcast_runner as pr

    calls = {"i": 0}

    async def fake_transcript_ainvoke(state, config=None):
        text = transcripts[min(calls["i"], len(transcripts) - 1)]
        calls["i"] += 1
        return {**dict(state), "transcript": [{"speaker": "m", "dialogue": text}]}

    async def fake_audio_ainvoke(state, config=None):
        return {**dict(state), "final_output_file_path": str(tmp_path / "out.mp3")}

    transcript_graph = MagicMock()
    transcript_graph.ainvoke = AsyncMock(side_effect=fake_transcript_ainvoke)
    audio_graph = MagicMock()
    audio_graph.ainvoke = AsyncMock(side_effect=fake_audio_ainvoke)
    monkeypatch.setattr(pr, "_transcript_graph", transcript_graph)
    monkeypatch.setattr(pr, "_audio_graph", audio_graph)

    episode_config = MagicMock()
    episode_config.default_briefing = "既定ブリーフィング"
    episode_config.num_segments = 3
    episode_config.language = None
    episode_config.transcript_provider = "anthropic"
    episode_config.transcript_model = "m"
    episode_config.transcript_config = {}
    import podcast_creator.episodes as episodes_mod
    import podcast_creator.speakers as speakers_mod

    monkeypatch.setattr(episodes_mod, "load_episode_config", lambda name: episode_config)
    monkeypatch.setattr(speakers_mod, "load_speaker_config", lambda name: MagicMock())

    events = []

    async def capture_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(pr, "_log_quality_event", capture_event)
    return pr, transcript_graph, audio_graph, events


@pytest.mark.asyncio
async def test_gate_passes_good_transcript_first_try(monkeypatch, tmp_path):
    pr, transcript_graph, audio_graph, events = _gate_test_setup(
        monkeypatch, tmp_path, [GOOD_TRANSCRIPT]
    )
    monkeypatch.delenv("SIDECAR_GATE", raising=False)
    result = await pr.create_podcast_single_pass(
        content=CHAPTER_CONTENT, briefing="b", episode_name="第1章",
        output_dir=str(tmp_path), speaker_config="s", episode_profile="p",
    )
    assert result["final_output_file_path"].endswith("out.mp3")
    assert transcript_graph.ainvoke.await_count == 1  # no retry
    assert audio_graph.ainvoke.await_count == 1
    assert events[0]["verdict"] == "passed"
    assert events[0]["kind"] == "transcript_gate"


@pytest.mark.asyncio
async def test_gate_retries_once_with_critique_then_passes(monkeypatch, tmp_path):
    pr, transcript_graph, audio_graph, events = _gate_test_setup(
        monkeypatch, tmp_path, [BAD_TRANSCRIPT, GOOD_TRANSCRIPT]
    )
    monkeypatch.delenv("SIDECAR_GATE", raising=False)
    await pr.create_podcast_single_pass(
        content=CHAPTER_CONTENT, briefing="b", episode_name="第1章",
        output_dir=str(tmp_path), speaker_config="s", episode_profile="p",
    )
    assert transcript_graph.ainvoke.await_count == 2
    # 2回目の呼び出しは批評が briefing に追記されている
    retry_state = transcript_graph.ainvoke.await_args_list[1].args[0]
    assert "品質レビュー指摘" in retry_state["briefing"]
    assert events[0]["verdict"] == "retried_passed"
    # 採用されたのは良い方の台本
    audio_state = audio_graph.ainvoke.await_args_list[0].args[0]
    assert "1つ目" in audio_state["transcript"][0]["dialogue"]


@pytest.mark.asyncio
async def test_gate_rejects_after_failed_retry(monkeypatch, tmp_path):
    pr, transcript_graph, audio_graph, events = _gate_test_setup(
        monkeypatch, tmp_path, [BAD_TRANSCRIPT, BAD_TRANSCRIPT]
    )
    monkeypatch.delenv("SIDECAR_GATE", raising=False)
    with pytest.raises(ValueError, match="品質ゲート未達"):
        await pr.create_podcast_single_pass(
            content=CHAPTER_CONTENT, briefing="b", episode_name="第1章",
            output_dir=str(tmp_path), speaker_config="s", episode_profile="p",
        )
    audio_graph.ainvoke.assert_not_awaited()  # TTS 費用をかけない
    assert events[0]["verdict"] == "rejected"


@pytest.mark.asyncio
async def test_gate_disabled_skips_scoring(monkeypatch, tmp_path):
    pr, transcript_graph, audio_graph, events = _gate_test_setup(
        monkeypatch, tmp_path, [BAD_TRANSCRIPT]
    )
    monkeypatch.setenv("SIDECAR_GATE", "0")
    result = await pr.create_podcast_single_pass(
        content=CHAPTER_CONTENT, briefing="b", episode_name="第1章",
        output_dir=str(tmp_path), speaker_config="s", episode_profile="p",
    )
    assert result["final_output_file_path"].endswith("out.mp3")
    assert events == []  # 採点なし
    assert transcript_graph.ainvoke.await_count == 1
    assert audio_graph.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_run_create_podcast_routes_on_env_flag(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from sidecar import podcast_runner as pr

    monkeypatch.setattr(pr, "_configure_podcast_creator", AsyncMock())
    two_pass = AsyncMock(return_value=None)
    one_pass = AsyncMock(return_value=None)
    monkeypatch.setattr(pr, "create_podcast", two_pass)
    monkeypatch.setattr(pr, "create_podcast_single_pass", one_pass)

    kwargs = dict(
        content="c", briefing="b", episode_name="e",
        output_dir=str(tmp_path), speaker_config="s", episode_profile="p",
    )
    # Single-pass is the default (measured better + cheaper); 0 opts out.
    monkeypatch.delenv("SIDECAR_SINGLE_PASS", raising=False)
    await pr.run_create_podcast(**kwargs)
    one_pass.assert_awaited_once()
    two_pass.assert_not_awaited()

    monkeypatch.setenv("SIDECAR_SINGLE_PASS", "0")
    await pr.run_create_podcast(**kwargs)
    two_pass.assert_awaited_once()
