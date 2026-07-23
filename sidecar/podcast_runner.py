"""Pure-Python podcast generation runner used by the gRPC sidecar.

This isolates the Python-only computation that has no Rust equivalent:
podcast-creator (outline LLM -> transcript LLM -> TTS). It mirrors the
profile-resolution + configure(...) + create_podcast(...) logic in
commands/podcast_commands.py, but is decoupled from surreal-commands and
from PodcastEpisode persistence (the Rust gateway owns persistence).

Kept import-light and independently testable (no grpc import here).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel

from open_notebook.database.repository import repo_query
from open_notebook.podcasts.models import _resolve_model_config

try:
    from podcast_creator import configure, create_podcast
except ImportError as e:  # pragma: no cover - environment guard
    logger.error(f"Failed to import podcast_creator: {e}")
    raise ValueError("podcast_creator library not available")


@dataclass
class CreatePodcastResult:
    final_output_file_path: Optional[str]
    transcript: Any
    outline: Any


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert podcast-creator's Pydantic objects (e.g.
    ValidatedDialogue) into plain JSON-serializable structures."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    return obj


async def _configure_podcast_creator() -> None:
    """Load every episode/speaker profile, resolve model+credential configs,
    and inject them into podcast-creator's global config.

    Mirrors commands/podcast_commands.py lines ~137-208. Profiles that fail to
    resolve are dropped so podcast-creator validation does not reject the batch.
    """
    episode_profiles = await repo_query("SELECT * FROM episode_profile")
    speaker_profiles = await repo_query("SELECT * FROM speaker_profile")

    episode_profiles_dict = {p["name"]: p for p in episode_profiles}
    speaker_profiles_dict = {p["name"]: p for p in speaker_profiles}

    for ep_name in list(episode_profiles_dict.keys()):
        ep = episode_profiles_dict[ep_name]
        try:
            if ep.get("outline_llm"):
                prov, model, conf = await _resolve_model_config(str(ep["outline_llm"]))
                ep["outline_provider"], ep["outline_model"], ep["outline_config"] = (
                    prov,
                    model,
                    conf,
                )
            if ep.get("transcript_llm"):
                prov, model, conf = await _resolve_model_config(
                    str(ep["transcript_llm"])
                )
                (
                    ep["transcript_provider"],
                    ep["transcript_model"],
                    ep["transcript_config"],
                ) = (prov, model, conf)
        except Exception as e:
            logger.warning(
                f"Dropping episode profile '{ep_name}' from config "
                f"(model resolution failed): {e}"
            )
            del episode_profiles_dict[ep_name]

    for sp_name in list(speaker_profiles_dict.keys()):
        sp = speaker_profiles_dict[sp_name]
        if sp.get("voice_model"):
            try:
                prov, model, conf = await _resolve_model_config(str(sp["voice_model"]))
                sp["tts_provider"], sp["tts_model"], sp["tts_config"] = (
                    prov,
                    model,
                    conf,
                )
            except Exception as e:
                logger.warning(
                    f"Dropping speaker profile '{sp_name}' from config "
                    f"(TTS resolution failed): {e}"
                )
                del speaker_profiles_dict[sp_name]
                continue
        for speaker in sp.get("speakers", []):
            if speaker.get("voice_model"):
                try:
                    prov, model, conf = await _resolve_model_config(
                        str(speaker["voice_model"])
                    )
                    speaker["tts_provider"], speaker["tts_model"], speaker[
                        "tts_config"
                    ] = (prov, model, conf)
                except Exception as e:
                    logger.warning(
                        f"Per-speaker TTS resolution failed for "
                        f"'{speaker.get('name')}': {e}"
                    )

    configure("speakers_config", {"profiles": speaker_profiles_dict})
    configure("episode_config", {"profiles": episode_profiles_dict})


async def run_create_podcast(
    *,
    content: str,
    briefing: str,
    episode_name: str,
    output_dir: str,
    speaker_config: str,
    episode_profile: str,
) -> CreatePodcastResult:
    """Generate one episode's audio (one outline -> N segments -> one mp3).

    The caller (Rust gateway) loops this per chapter for audiobooks and owns
    the output_dir (a per-episode UUID directory) and persistence.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    await _configure_podcast_creator()

    logger.info(f"Sidecar create_podcast: episode_name={episode_name}")
    result = await create_podcast(
        content=content,
        briefing=briefing,
        episode_name=episode_name,
        output_dir=output_dir,
        speaker_config=speaker_config,
        episode_profile=episode_profile,
    )

    return CreatePodcastResult(
        final_output_file_path=(
            str(result.get("final_output_file_path")) if result else None
        ),
        transcript=_to_jsonable(result.get("transcript")) if result else None,
        outline=_to_jsonable(result.get("outline")) if result else None,
    )
