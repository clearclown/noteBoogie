"""gRPC sidecar server exposing Python-only podcast computation to the Rust gateway.

Run:  uv run python -m sidecar.podcast_sidecar   (or: make sidecar)
Env:  SIDECAR_ADDRESS (default [::]:50069)

Implements protos/podcast.proto (noteboogie.podcast.v1.PodcastSidecar):
  - Ping          liveness probe
  - CreatePodcast wraps podcast-creator (outline/transcript LLM + TTS)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from loguru import logger

# Generated stubs do `import podcast_pb2`; put the gen dir on sys.path so the
# grpc module's sibling import resolves whether or not it's imported as a package.
_GEN_DIR = Path(__file__).resolve().parent / "gen"
if str(_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GEN_DIR))

import grpc  # noqa: E402
import podcast_pb2  # noqa: E402  (from sidecar/gen)
import podcast_pb2_grpc  # noqa: E402  (from sidecar/gen)

from sidecar.podcast_runner import run_create_podcast  # noqa: E402

DEFAULT_ADDRESS = os.getenv("SIDECAR_ADDRESS", "[::]:50069")
SIDECAR_VERSION = "0.1.0"


class PodcastSidecarServicer(podcast_pb2_grpc.PodcastSidecarServicer):
    async def Ping(self, request, context):  # noqa: N802 (gRPC naming)
        return podcast_pb2.PingResponse(ok=True, version=SIDECAR_VERSION)

    async def CreatePodcast(self, request, context):  # noqa: N802
        try:
            result = await run_create_podcast(
                content=request.content,
                briefing=request.briefing,
                episode_name=request.episode_name,
                output_dir=request.output_dir,
                speaker_config=request.speaker_config,
                episode_profile=request.episode_profile,
            )
        except ValueError as e:
            # Permanent/validation error -> INVALID_ARGUMENT
            logger.error(f"CreatePodcast invalid argument: {e}")
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except Exception as e:  # pragma: no cover - surfaced to caller
            logger.exception("CreatePodcast failed")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

        return podcast_pb2.CreatePodcastResponse(
            final_output_file_path=result.final_output_file_path or "",
            transcript_json=json.dumps(result.transcript, ensure_ascii=False)
            if result.transcript is not None
            else "",
            outline_json=json.dumps(result.outline, ensure_ascii=False)
            if result.outline is not None
            else "",
        )


async def serve(address: str = DEFAULT_ADDRESS) -> None:
    server = grpc.aio.server()
    podcast_pb2_grpc.add_PodcastSidecarServicer_to_server(
        PodcastSidecarServicer(), server
    )
    server.add_insecure_port(address)
    await server.start()
    logger.info(f"Podcast sidecar listening on {address} (v{SIDECAR_VERSION})")
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
