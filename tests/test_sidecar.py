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
