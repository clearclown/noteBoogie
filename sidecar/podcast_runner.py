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


def build_synthetic_outline(num_segments: int):
    """Fixed Book Navigator outline (課題 → 3要点 → アクションプラン).

    The briefing already dictates this exact structure, so paying an outline
    LLM call per chapter (which re-reads the whole chapter) is redundant for
    single-speaker monologues. Used by the single-pass path.
    """
    from podcast_creator.core import Outline, Segment

    segments = [
        Segment(
            name="導入",
            description="この章が解決するビジネス上の課題を提示し、これから話す3つの要点を予告する",
            size="short",
        ),
        Segment(
            name="本編",
            description="重要な3つのコンセプトを『1つ目は』『2つ目は』『3つ目は』と番号を数えながら、結論→説明の順で語る",
            size="long",
        ),
        Segment(
            name="アクションプラン",
            description="明日からそのまま真似できる手順を1ステップずつ具体的に示して締める",
            size="medium",
        ),
    ]
    return Outline(segments=segments[: max(1, min(num_segments, len(segments)))] if num_segments < 3 else segments)


def _build_single_pass_graph():
    """Compile the podcast graph WITHOUT the outline node (transcript → TTS)."""
    from langgraph.graph import END, START, StateGraph
    from podcast_creator.nodes import (
        combine_audio_node,
        generate_all_audio_node,
        generate_transcript_node,
        route_audio_generation,
    )
    from podcast_creator.state import PodcastState

    workflow = StateGraph(PodcastState)
    workflow.add_node("generate_transcript", generate_transcript_node)
    workflow.add_node("generate_all_audio", generate_all_audio_node)
    workflow.add_node("combine_audio", combine_audio_node)
    workflow.add_edge(START, "generate_transcript")
    workflow.add_conditional_edges(
        "generate_transcript", route_audio_generation, ["generate_all_audio"]
    )
    workflow.add_edge("generate_all_audio", "combine_audio")
    workflow.add_edge("combine_audio", END)
    return workflow.compile()


_single_pass_graph = None


async def create_podcast_single_pass(
    *,
    content: str,
    briefing: str,
    episode_name: str,
    output_dir: str,
    speaker_config: str,
    episode_profile: str,
) -> dict:
    """Single-LLM-pass variant of podcast_creator.create_podcast.

    Skips the outline LLM call by injecting the fixed Book Navigator outline;
    everything else (transcript LLM, per-segment TTS, mp3 assembly) reuses
    podcast-creator's own nodes. Roughly halves script-LLM input cost and
    removes one model round-trip per chapter.
    """
    from pathlib import Path as _Path

    from podcast_creator.episodes import load_episode_config
    from podcast_creator.language import resolve_language_name
    from podcast_creator.speakers import load_speaker_config
    from podcast_creator.state import PodcastState

    global _single_pass_graph
    if _single_pass_graph is None:
        _single_pass_graph = _build_single_pass_graph()

    episode_config = load_episode_config(episode_profile)
    output_path = _Path(output_dir)
    output_path.mkdir(exist_ok=True, parents=True)

    initial_state = PodcastState(
        content=content,
        briefing=briefing or episode_config.default_briefing,
        num_segments=episode_config.num_segments or 3,
        language=(
            resolve_language_name(episode_config.language)
            if episode_config.language
            else None
        ),
        outline=build_synthetic_outline(episode_config.num_segments or 3),
        transcript=[],
        audio_clips=[],
        final_output_file_path=None,
        output_dir=output_path,
        episode_name=episode_name,
        speaker_profile=load_speaker_config(speaker_config),
    )
    config = {
        "configurable": {
            "transcript_provider": episode_config.transcript_provider,
            "transcript_model": episode_config.transcript_model,
            "transcript_config": episode_config.transcript_config,
        }
    }
    return await _single_pass_graph.ainvoke(initial_state, config=config)


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

    _sanitize_profiles(episode_profiles_dict, speaker_profiles_dict)
    configure("speakers_config", {"profiles": speaker_profiles_dict})
    configure("episode_config", {"profiles": episode_profiles_dict})


def _sanitize_profiles(
    episode_profiles_dict: dict, speaker_profiles_dict: dict
) -> None:
    """Drop profiles that cannot pass podcast-creator's strict validation.

    Since migration 22 removed the legacy provider/model strings, profiles
    without a linked model resolve to dicts missing tts_provider/tts_model
    (speakers) or transcript_provider/transcript_model (episodes). Newer
    podcast-creator validates the WHOLE profiles dict, so one unconfigured
    upstream seed profile (e.g. business_panel) fails every generation.
    DB datetime fields are stripped too (not JSON-serializable downstream).
    """
    for profiles in (episode_profiles_dict, speaker_profiles_dict):
        for profile in profiles.values():
            profile.pop("created", None)
            profile.pop("updated", None)

    for sp_name in list(speaker_profiles_dict.keys()):
        sp = speaker_profiles_dict[sp_name]
        if not (sp.get("tts_provider") and sp.get("tts_model")):
            logger.debug(f"Dropping speaker profile '{sp_name}' (no resolved TTS)")
            del speaker_profiles_dict[sp_name]

    for ep_name in list(episode_profiles_dict.keys()):
        ep = episode_profiles_dict[ep_name]
        if not (ep.get("transcript_provider") and ep.get("transcript_model")):
            logger.debug(f"Dropping episode profile '{ep_name}' (no resolved LLM)")
            del episode_profiles_dict[ep_name]


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
    import os

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    await _configure_podcast_creator()

    # Single-pass (no outline LLM) is the DEFAULT: measured on the full book
    # it scored higher than two-pass (reward 0.83-0.90 vs 0.72 avg), halves
    # script-LLM input cost, and eliminates the outline node's structured-
    # JSON failures on long chapters. SIDECAR_SINGLE_PASS=0 opts out.
    single_pass = os.getenv("SIDECAR_SINGLE_PASS", "1").lower() in ("1", "true", "yes")
    logger.info(
        f"Sidecar create_podcast: episode_name={episode_name} single_pass={single_pass}"
    )
    if single_pass:
        result = await create_podcast_single_pass(
            content=content,
            briefing=briefing,
            episode_name=episode_name,
            output_dir=output_dir,
            speaker_config=speaker_config,
            episode_profile=episode_profile,
        )
    else:
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
