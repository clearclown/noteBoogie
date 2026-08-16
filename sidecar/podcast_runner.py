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


# この文字数未満の章は「薄い章」とみなし、固定の3部構成を強制しない。
# 中扉・はじめに・短いコラム等をフルの導入→3要点→アクションプランに無理やり
# 合わせると、LLM が本文に無い内容を創作して水増しし grounding が落ちてゲート
# 棄却される（実測 grounding 0.19 / 長さ比 12.38）。短い章は短い台本でよい。
SHORT_CHAPTER_CHARS = 1500


def _is_thin_chapter(content: str) -> bool:
    import os

    try:
        threshold = int(os.getenv("SIDECAR_SHORT_CHAPTER_CHARS", str(SHORT_CHAPTER_CHARS)))
    except ValueError:
        threshold = SHORT_CHAPTER_CHARS
    return len((content or "").strip()) < threshold


def build_thin_chapter_outline():
    """薄い章向けの最小アウトライン。固定構成を課さず、内容量なりに簡潔に。"""
    from podcast_creator.core import Outline, Segment

    return Outline(
        segments=[
            Segment(
                name="要点",
                description=(
                    "この章に実際に書かれている内容だけを、簡潔にまとめる。"
                    "本文に無い話を創作して長さを埋めない。固定の構成（3つの要点や"
                    "アクションプラン）に無理に合わせず、短くてよい。"
                ),
                size="short",
            )
        ]
    )


# 薄い章の briefing に添える指示。ゲート(grounding重視)を素直に通すため、
# 「短くてよい・創作禁止」を明示する。
THIN_CHAPTER_BRIEFING_NOTE = (
    "\n\n## この章について（重要）\n"
    "この章は内容が短い。書かれていることだけを簡潔にまとめること。"
    "3つの要点やアクションプランといった固定の型に無理に合わせたり、"
    "本文に無い内容を創作して長さを水増ししたりしてはならない。"
    "内容が少なければ台本も短くてよい。"
)


def _build_transcript_graph():
    """Transcript-only graph (no outline node, no TTS) — the gate scores here."""
    from langgraph.graph import END, START, StateGraph
    from podcast_creator.nodes import generate_transcript_node
    from podcast_creator.state import PodcastState

    workflow = StateGraph(PodcastState)
    workflow.add_node("generate_transcript", generate_transcript_node)
    workflow.add_edge(START, "generate_transcript")
    workflow.add_edge("generate_transcript", END)
    return workflow.compile()


def _build_audio_graph():
    """TTS + assembly graph, entered only after the transcript passes the gate."""
    from langgraph.graph import END, START, StateGraph
    from podcast_creator.nodes import combine_audio_node, generate_all_audio_node
    from podcast_creator.state import PodcastState

    workflow = StateGraph(PodcastState)
    workflow.add_node("generate_all_audio", generate_all_audio_node)
    workflow.add_node("combine_audio", combine_audio_node)
    workflow.add_edge(START, "generate_all_audio")
    workflow.add_edge("generate_all_audio", "combine_audio")
    workflow.add_edge("combine_audio", END)
    return workflow.compile()


_transcript_graph = None
_audio_graph = None


# ---------------------------------------------------------------------------
# Transcript quality gate (ADVANCED_ROADMAP §4-1): score before spending TTS
# money. Regex-based metrics — zero extra LLM cost; a retry costs exactly one
# transcript-LLM call and only happens below threshold.
# ---------------------------------------------------------------------------


def gate_enabled() -> bool:
    import os

    return os.getenv("SIDECAR_GATE", "1").lower() in ("1", "true", "yes")


def gate_threshold() -> float:
    import os

    try:
        return float(os.getenv("SIDECAR_GATE_THRESHOLD", "0.6"))
    except ValueError:
        return 0.6


def transcript_max_attempts() -> int:
    """台本LLMの再試行回数（既定5）。壊れた構造化出力への保険。

    事前修復([`_repair_transcript_keys`])が構造的に有効な JSON のキー破損は
    第1試行で直すので、リトライは修復で拾えない重度の破損だけを引き受ける。
    """
    import os

    try:
        return max(1, int(os.getenv("SIDECAR_TRANSCRIPT_ATTEMPTS", "5")))
    except ValueError:
        return 5


def _repair_transcript_keys(content):
    """台本LLMが壊した `"dialogue"` キーを、パース前に dict レベルで修復する。

    実測では `"dialogue"` が `"dialogே"`/`"dialogine"`/`"dialogume"` のように
    末尾数文字だけ化ける。JSON 自体は構造的に有効（壊れキーも妥当な JSON 文字列）
    なので、`json.loads` はできるが ValidatedTranscript の Pydantic 検証で
    `dialogue` フィールド欠落として落ちる。各セグメントは `{"speaker", "dialogue"}`
    の2キーのはず — `speaker` があり `dialogue` が無く、非 speaker キーがちょうど
    1つなら、それを `dialogue` へ改名する。修復不要なら原文字列をそのまま返す
    （マークダウンフェンス等の既存挙動を壊さない）。
    """
    import json
    import re

    if not isinstance(content, str):
        return content
    try:
        data = json.loads(content)
    except Exception:  # noqa: BLE001 - フェンス/前後テキスト付きは最初の {…} を試す
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return content
        try:
            data = json.loads(m.group(0))
        except Exception:  # noqa: BLE001 - 修復不能なら原文のまま下流に委ねる
            return content
    segs = data.get("transcript") if isinstance(data, dict) else None
    if not isinstance(segs, list):
        return content
    changed = False
    for seg in segs:
        if not isinstance(seg, dict) or "dialogue" in seg or "speaker" not in seg:
            continue
        others = [k for k in seg if k != "speaker"]
        if len(others) == 1:
            seg["dialogue"] = seg.pop(others[0])
            changed = True
    return json.dumps(data, ensure_ascii=False) if changed else content


def _install_tts_parts_guard() -> None:
    """Google TTS(gemini-tts)の「parts欠落」応答による章失敗をリトライで吸収する。

    esperanto の GoogleTextToSpeechModel.agenerate_speech は Gemini 応答から
    `candidates[0].content.parts[0].inlineData.data` を無ガードで取り出すため、
    Gemini が稀に parts の無い候補（一時的な空応答・安全フィルタ等）を返すと
    KeyError('parts') で**章全体が失敗**する（再生成バッチで多発）。1クリップの
    一過性失敗なので、引き直せば通ることが多い。ライブラリは非改変で、メソッドを
    リトライ付きに差し替える。冪等。
    """
    try:
        from esperanto.providers.tts.google import GoogleTextToSpeechModel
    except Exception as e:  # noqa: BLE001 - 環境差で無ければ何もしない
        logger.warning(f"TTS parts guard 未適用（esperanto未検出）: {e}")
        return

    orig = GoogleTextToSpeechModel.agenerate_speech
    if getattr(orig, "_parts_guarded", False):
        return

    import asyncio as _asyncio

    async def guarded(self, *args, **kwargs):
        last: Exception | None = None
        for attempt in range(4):
            try:
                return await orig(self, *args, **kwargs)
            except KeyError as e:  # parts / candidates 欠落は一過性として再試行
                if "parts" not in str(e) and "candidates" not in str(e):
                    raise
                last = e
                logger.warning(
                    f"TTS応答に parts が無い（{attempt + 1}/4回目）。再試行します。"
                )
                await _asyncio.sleep(1.0 * (attempt + 1))
        raise ValueError(f"TTS応答に parts が無い状態が4回続きました: {last}")

    guarded._parts_guarded = True  # type: ignore[attr-defined]
    GoogleTextToSpeechModel.agenerate_speech = guarded  # type: ignore[method-assign]


def _install_transcript_key_repair() -> None:
    """podcast_creator の transcript パーサに壊れキー事前修復を差し込む。

    ライブラリは非改変。`create_validated_transcript_parser` を包み、返すパーサの
    `.invoke` の前段で [`_repair_transcript_keys`] を通す。nodes.py が import 済みの
    名前を差し替えるので、単一パス/二パス両経路の generate_transcript_node に効く。
    冪等（二重適用しない）。
    """
    from langchain_core.runnables import RunnableLambda
    from podcast_creator import nodes as pc_nodes

    orig = pc_nodes.create_validated_transcript_parser
    if getattr(orig, "_key_repair_wrapped", False):
        return

    def wrapped(valid_speaker_names):
        parser = orig(valid_speaker_names)
        return RunnableLambda(
            lambda content: parser.invoke(_repair_transcript_keys(content))
        )

    wrapped._key_repair_wrapped = True  # type: ignore[attr-defined]
    pc_nodes.create_validated_transcript_parser = wrapped


async def _generate_transcript(make_state_fn, briefing: str, config: dict):
    """台本グラフを実行し、一過性の生成失敗はリトライする。

    台本LLMはまれに壊れた構造化出力を返す。実測では `"dialogue"` のはずのキーが
    `"dialogே"`（タミル文字混入）や `"dialogio"` になり、ValidatedTranscript の
    パース（LangChain OUTPUT_PARSING_FAILURE）が例外化して章まるごと落ちていた。
    各試行は独立した LLM ドローなので、引き直せばほぼ正しくパースできる。品質ゲート
    のスコアリング再生成（低品質時）とは別レイヤの「そもそも生成に失敗した時」の保険。
    """
    attempts = transcript_max_attempts()
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            assert _transcript_graph is not None  # 呼び出し前に必ず構築済み
            return await _transcript_graph.ainvoke(make_state_fn(briefing), config=config)
        except Exception as e:  # noqa: BLE001 - transient LLM/parse failures are retryable
            last_exc = e
            logger.warning(
                f"transcript generation attempt {i + 1}/{attempts} failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
    raise ValueError(
        f"台本生成が{attempts}回とも失敗しました"
        f"（最後のエラー: {type(last_exc).__name__}: {str(last_exc)[:300]}）"
    )


# 薄い章の合格に必要な grounding 下限（捏造していないかだけを見る）。
# 薄章は台本が短く fact-term 数も少ないため grounding 指標がノイジー。0.6 は
# 「4割以上が本文由来」＝明白な捏造だけを弾く緩めの線（実測 0.67 を許容する）。
GATE_THIN_GROUNDING_FLOOR = 0.6


def build_gate_critique(ev, threshold: float, thin: bool = False) -> str:
    """未達指標を台本LLMへの日本語の改善指示に変換する（再生成1回で使う）。

    thin（薄い章）のときは構成・長さの指摘を出さない。フルの3部構成を求めると
    簡潔化と矛盾して水増し→捏造を招くため、捏造の除去（grounding）だけを促す。
    """
    if thin:
        terms = "、".join(str(t) for t in ev.unsupported_terms[:10])
        lines = [
            "## 品質レビュー指摘（前回の台本は本文に無い内容を創作していた。作り直すこと）",
            "- この章は内容が短い。書かれていることだけを簡潔にまとめ、短くてよい。",
            "- 固定の構成（3つの要点・アクションプラン）に無理に合わせないこと。",
        ]
        if terms:
            lines.append(f"- 捏造禁止: 次の語は本文に無い。使わないこと: {terms}")
        if ev.politeness < 0.9:
            lines.append("- 文体: 文末は です/ます調で統一すること")
        return "\n".join(lines)

    lines = [
        "## 品質レビュー指摘（前回の台本は品質ゲート未達。以下を必ず反映して作り直すこと）",
        f"- 前回スコア: {ev.composite:.2f}（合格ライン {threshold:.2f}）",
    ]
    if ev.structure < 1.0:
        lines.append(
            "- 構成: 冒頭で「この章は〜という悩み/課題に答えます」と課題を提示し、"
            "本編は「1つ目は」「2つ目は」「3つ目は」と番号を数えながら順に述べ、"
            "最後に「アクションプラン」で締めること"
        )
    if ev.grounding < 0.95 and ev.unsupported_terms:
        terms = "、".join(str(t) for t in ev.unsupported_terms[:10])
        lines.append(
            f"- 捏造禁止: 次の語は章本文に存在しない。使わないか、本文にある表現へ置き換えること: {terms}"
        )
    if ev.politeness < 0.9:
        lines.append("- 文体: 文末は です/ます調で統一すること")
    if ev.length_ratio < 1.0:
        lines.append("- 分量: 台本が薄すぎる。章本文の論点を漏らさず、具体例を添えて膨らませること")
    elif ev.length_ratio > 8.0:
        lines.append("- 分量: 台本が本文に比して長すぎる。本文に無い話を足して水増ししないこと")
    return "\n".join(lines)


def gate_decision(evals: list, threshold: float) -> tuple[int, bool]:
    """試行のうち composite 最大のものを選び、(index, 合格か) を返す。"""
    best_index = max(range(len(evals)), key=lambda i: evals[i].composite)
    return best_index, evals[best_index].composite >= threshold


async def _log_quality_event(
    kind: str, name: str, score: float, verdict: str, details: dict
) -> None:
    """quality_event へ判定を記録する（閾値較正・RL報酬蒸留のデータ源、best-effort）。"""
    from open_notebook.database.repository import repo_insert

    try:
        await repo_insert(
            "quality_event",
            [
                {
                    "kind": kind,
                    "name": name[:200],
                    "score": round(float(score), 3),
                    "verdict": verdict,
                    "details": details,
                }
            ],
        )
    except Exception as e:  # noqa: BLE001 - logging must never break generation
        logger.warning(f"quality_event insert failed: {e}")


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

    global _transcript_graph, _audio_graph
    if _transcript_graph is None:
        _transcript_graph = _build_transcript_graph()
    if _audio_graph is None:
        _audio_graph = _build_audio_graph()

    episode_config = load_episode_config(episode_profile)
    output_path = _Path(output_dir)
    output_path.mkdir(exist_ok=True, parents=True)

    effective_briefing = briefing or episode_config.default_briefing

    # 薄い章は固定構成を強制せず、内容量に比例した短い台本にする（捏造→ゲート棄却の回避）。
    thin = _is_thin_chapter(content)
    if thin:
        effective_briefing = effective_briefing + THIN_CHAPTER_BRIEFING_NOTE
        logger.info(
            f"thin chapter ({len(content.strip())} chars < threshold): "
            f"using brief content-proportional outline"
        )

    def make_state(state_briefing: str) -> PodcastState:
        return PodcastState(
            content=content,
            briefing=state_briefing,
            num_segments=1 if thin else (episode_config.num_segments or 3),
            language=(
                resolve_language_name(episode_config.language)
                if episode_config.language
                else None
            ),
            outline=(
                build_thin_chapter_outline()
                if thin
                else build_synthetic_outline(episode_config.num_segments or 3)
            ),
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

    # 段階1: transcript のみ生成し、TTS 前に採点する
    # （壊れた構造化出力による一過性失敗はここでリトライ吸収する）
    state = await _generate_transcript(make_state, effective_briefing, config)

    if gate_enabled():
        from scripts.eval_transcript import evaluate_chapter, transcript_text

        threshold = gate_threshold()

        def score(s):
            return evaluate_chapter(
                episode_name, content, transcript_text(_to_jsonable(s.get("transcript")))
            )

        def passes(ev) -> bool:
            # 薄い章は構成・長さを問わない。捏造していない(grounding)＋敬体だけ見る。
            # フルの3部構成を求めると、簡潔化と矛盾して水増し→捏造を招くため。
            if thin:
                return ev.grounding >= GATE_THIN_GROUNDING_FLOOR and ev.politeness >= 0.7
            return ev.composite >= threshold

        attempts = [state]
        evals = [score(state)]
        if not passes(evals[0]):
            critique = build_gate_critique(evals[0], threshold, thin=thin)
            logger.info(
                f"Gate: rejected (thin={thin}, composite={evals[0].composite:.2f}, "
                f"grounding={evals[0].grounding:.2f}), regenerating once with critique"
            )
            retry_state = await _generate_transcript(
                make_state, f"{effective_briefing}\n\n{critique}", config
            )
            attempts.append(retry_state)
            evals.append(score(retry_state))
        # 薄い章は grounding 最大、通常は composite 最大を採用
        if thin:
            best_index = max(range(len(evals)), key=lambda i: evals[i].grounding)
            passed = passes(evals[best_index])
        else:
            best_index, passed = gate_decision(evals, threshold)
        best_eval = evals[best_index]
        verdict = (
            "passed"
            if passed and len(evals) == 1
            else "retried_passed"
            if passed
            else "rejected"
        )
        await _log_quality_event(
            kind="transcript_gate",
            name=episode_name,
            score=best_eval.composite,
            verdict=verdict,
            details={
                "threshold": threshold,
                "thin": thin,
                "attempts": [e.composite for e in evals],
                "structure": best_eval.structure,
                "grounding": best_eval.grounding,
                "politeness": best_eval.politeness,
                "length_ratio": best_eval.length_ratio,
                "unsupported_terms": best_eval.unsupported_terms[:10],
            },
        )
        if not passed:
            # ValueError → gRPC INVALID_ARGUMENT → gateway が generation_error に記録
            reason = (
                f"捏造過多(grounding={best_eval.grounding:.2f})"
                if thin
                else f"composite={best_eval.composite:.2f} (閾値 {threshold:.2f})"
            )
            raise ValueError(
                f"品質ゲート未達: {reason}。"
                f"構成{best_eval.structure:.2f}/グラウンディング{best_eval.grounding:.2f}/"
                f"敬体{best_eval.politeness:.2f}/長さ比{best_eval.length_ratio}"
            )
        state = attempts[best_index]

    # 段階2: 合格した transcript だけに TTS 費用をかける
    return await _audio_graph.ainvoke(state, config=config)


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
    _install_transcript_key_repair()
    _install_tts_parts_guard()
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
