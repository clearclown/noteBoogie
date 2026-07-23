"""Service layer for the mentor (師匠) UI — Book Navigator fork.

The mentor graph (recall→respond→memorize) keeps only gists in its own
mentor_memory table, so this layer persists the display log (mentor_message),
turns answers into speech, and manages the per-book learning weights that the
recall node applies (MENTOR_UI_DESIGN.md §3/§12).
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from open_notebook.exceptions import (
    ConfigurationError,
    InvalidInputError,
    NotFoundError,
)

MENTOR_AUDIO_DIR = Path(os.getenv("DATA_FOLDER", "./data")) / "podcasts" / "mentor"
MENTOR_TTS_VOICE = os.getenv("MENTOR_TTS_VOICE", "kore")
# 直近何件の記憶から自動傾斜を推定するか
AUTO_FACTOR_MEMORY_WINDOW = 50


class MentorSourceRef(BaseModel):
    id: str
    title: str


class MentorConsultRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class MentorConsultResponse(BaseModel):
    answer: str
    sources: List[MentorSourceRef]
    message_id: Optional[str] = None


class MentorMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    sources: Optional[List[str]] = None
    created: Optional[str] = None


class MentorMemoryResponse(BaseModel):
    id: str
    question: str
    gist: str
    sources: Optional[List[str]] = None
    created: Optional[str] = None


class MentorWeightEntry(BaseModel):
    source_id: str
    title: str
    weight: float = 1.0
    chapter_weights: Optional[Dict[str, float]] = None
    auto_factor: float = 1.0
    chapters: List[str] = Field(default_factory=list)


class MentorWeightUpdateRequest(BaseModel):
    weight: float = Field(ge=0.0, le=2.0)
    chapter_weights: Optional[Dict[str, float]] = None


class MentorPersonaResponse(BaseModel):
    persona: str
    is_default: bool


class MentorPersonaUpdateRequest(BaseModel):
    persona: str = Field(min_length=10, max_length=4000)


def extract_source_refs(search_results: List[Dict[str, Any]]) -> List[MentorSourceRef]:
    """recall のヒットから重複なしの参照本チップを組み立てる（順序維持）。"""
    refs: List[MentorSourceRef] = []
    seen = set()
    for hit in search_results or []:
        source_id = str(hit.get("parent_id") or hit.get("id") or "")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        refs.append(MentorSourceRef(id=source_id, title=str(hit.get("title") or source_id)))
    return refs


def strip_markdown_for_speech(text: str) -> str:
    """TTS が記号を読み上げないよう Markdown 装飾を落とす。"""
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]*)\*", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def audio_cache_path(message_id: str) -> Path:
    """メッセージIDからキャッシュ先を決める（record key 部分のみ使用、traversal 防止）。"""
    key = message_id.split(":", 1)[-1]
    safe = re.sub(r"[^A-Za-z0-9_-]", "", key)
    if not safe:
        raise InvalidInputError(f"Invalid message id: {message_id}")
    return MENTOR_AUDIO_DIR / f"{safe}.mp3"


class MentorService:
    @staticmethod
    async def consult(message: str) -> MentorConsultResponse:
        """mentor グラフを実行し、表示用の生ログ2行（user/mentor）を保存する。"""
        from open_notebook.database.repository import repo_create
        from open_notebook.graphs.mentor import graph

        result = await graph.ainvoke(  # type: ignore[call-overload]
            {"message": message}
        )
        answer = result.get("answer") or ""
        refs = extract_source_refs(result.get("search_results") or [])

        message_id: Optional[str] = None
        try:
            await repo_create("mentor_message", {"role": "user", "content": message})
            mentor_row = await repo_create(
                "mentor_message",
                {
                    "role": "mentor",
                    "content": answer,
                    "sources": [r.id for r in refs] or None,
                },
            )
            raw_id = mentor_row.get("id") if isinstance(mentor_row, dict) else None
            message_id = str(raw_id) if raw_id else None
        except Exception as e:  # noqa: BLE001 - log persistence is best-effort
            logger.warning(f"mentor message log failed: {e}")

        return MentorConsultResponse(answer=answer, sources=refs, message_id=message_id)

    @staticmethod
    async def speak(message_id: str) -> Path:
        """師匠回答をTTSで音声化し mp3 パスを返す（生成結果はキャッシュ）。"""
        from open_notebook.ai.models import model_manager
        from open_notebook.database.repository import repo_query

        path = audio_cache_path(message_id)
        if path.exists() and path.stat().st_size > 0:
            return path

        rows = await repo_query(
            "SELECT content, role FROM mentor_message WHERE id = type::thing($id)",
            {"id": message_id},
        )
        if not rows:
            raise NotFoundError(f"Mentor message not found: {message_id}")
        text = strip_markdown_for_speech(str(rows[0].get("content") or ""))
        if not text:
            raise InvalidInputError("Message has no speakable content")

        tts = await model_manager.get_text_to_speech()
        if not tts:
            raise ConfigurationError(
                "No default text-to-speech model configured. Set one in Models settings."
            )
        audio = await tts.agenerate_speech(text=text, voice=MENTOR_TTS_VOICE)
        content = getattr(audio, "content", None) or b""
        if not content:
            raise ConfigurationError("Text-to-speech returned empty audio")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    @staticmethod
    async def list_messages(limit: int = 50) -> List[MentorMessageResponse]:
        from open_notebook.database.repository import repo_query

        rows = await repo_query(
            "SELECT type::string(id) AS id, role, content, sources, "
            "type::string(created) AS created "
            "FROM mentor_message ORDER BY created DESC LIMIT $n",
            {"n": limit},
        )
        # 新しい順で取り、表示は古い→新しい順
        return [MentorMessageResponse(**row) for row in reversed(rows)]

    @staticmethod
    async def list_memories(limit: int = 20) -> List[MentorMemoryResponse]:
        from open_notebook.database.repository import repo_query

        rows = await repo_query(
            "SELECT type::string(id) AS id, question, gist, sources, "
            "type::string(created) AS created "
            "FROM mentor_memory ORDER BY created DESC LIMIT $n",
            {"n": limit},
        )
        return [MentorMemoryResponse(**row) for row in rows]

    @staticmethod
    async def delete_memory(memory_id: str) -> None:
        from open_notebook.database.repository import repo_delete, repo_query

        if not memory_id.startswith("mentor_memory:"):
            raise InvalidInputError(f"Not a mentor memory id: {memory_id}")
        rows = await repo_query(
            "SELECT id FROM mentor_memory WHERE id = type::thing($id)", {"id": memory_id}
        )
        if not rows:
            raise NotFoundError(f"Mentor memory not found: {memory_id}")
        await repo_delete(memory_id)

    @staticmethod
    async def get_persona() -> MentorPersonaResponse:
        """現在のペルソナ（未設定ならドメイン非依存の既定）。"""
        from open_notebook.database.repository import repo_query
        from open_notebook.graphs.mentor import DEFAULT_PERSONA

        rows = await repo_query(
            "SELECT persona FROM mentor_profile WHERE name = 'default'"
        )
        stored = str(rows[0].get("persona")) if rows and rows[0].get("persona") else None
        return MentorPersonaResponse(
            persona=stored or DEFAULT_PERSONA, is_default=stored is None
        )

    @staticmethod
    async def update_persona(request: MentorPersonaUpdateRequest) -> MentorPersonaResponse:
        """ペルソナを差し替える（相談・スライドレビュー両方に即時反映）。"""
        from open_notebook.database.repository import repo_query

        await repo_query(
            "UPSERT mentor_profile SET name = 'default', persona = $persona "
            "WHERE name = 'default'",
            {"persona": request.persona},
        )
        return MentorPersonaResponse(persona=request.persona, is_default=False)

    @staticmethod
    async def get_weights() -> List[MentorWeightEntry]:
        """全蔵書の {手動傾斜, 章傾斜, 自動係数, 章タイトル} を返す（⚖️タブ用）。"""
        from open_notebook.database.repository import repo_query
        from open_notebook.graphs.mentor import compute_auto_factors

        sources = await repo_query(
            "SELECT type::string(id) AS id, title FROM source ORDER BY title"
        )
        weight_rows = await repo_query(
            "SELECT type::string(source) AS source_id, weight, chapter_weights "
            "FROM mentor_source_weight"
        )
        weights = {r["source_id"]: r for r in weight_rows}

        memories = await repo_query(
            "SELECT sources FROM mentor_memory ORDER BY created DESC LIMIT $n",
            {"n": AUTO_FACTOR_MEMORY_WINDOW},
        )
        auto = compute_auto_factors([m.get("sources") for m in memories])

        chapter_rows = await repo_query(
            "SELECT chapter_index, chapter_title, audiobook.source_id AS source_id "
            "FROM episode WHERE audiobook != NONE AND chapter_index != NONE "
            "ORDER BY chapter_index"
        )
        chapters: Dict[str, List[str]] = {}
        for row in chapter_rows:
            sid = str(row.get("source_id") or "")
            if sid:
                chapters.setdefault(sid, []).append(str(row.get("chapter_title") or ""))

        entries = []
        for src in sources:
            sid = src["id"]
            w = weights.get(sid, {})
            entries.append(
                MentorWeightEntry(
                    source_id=sid,
                    title=str(src.get("title") or sid),
                    weight=float(w.get("weight", 1.0)),
                    chapter_weights=w.get("chapter_weights"),
                    auto_factor=round(auto.get(sid, 1.0), 3),
                    chapters=chapters.get(sid, []),
                )
            )
        return entries

    @staticmethod
    async def update_weight(
        source_id: str, request: MentorWeightUpdateRequest
    ) -> MentorWeightEntry:
        from open_notebook.database.repository import repo_query

        if not source_id.startswith("source:"):
            raise InvalidInputError(f"Not a source id: {source_id}")
        exists = await repo_query(
            "SELECT id, title FROM source WHERE id = type::thing($id)", {"id": source_id}
        )
        if not exists:
            raise NotFoundError(f"Source not found: {source_id}")

        if request.chapter_weights is not None:
            for key, value in request.chapter_weights.items():
                if not (0.0 <= float(value) <= 2.0):
                    raise InvalidInputError(
                        f"Chapter weight out of range for chapter {key}: {value}"
                    )

        await repo_query(
            "UPSERT mentor_source_weight SET source = type::thing($source), "
            "weight = $weight, chapter_weights = $cw WHERE source = type::thing($source)",
            {
                "source": source_id,
                "weight": request.weight,
                "cw": request.chapter_weights,
            },
        )
        return MentorWeightEntry(
            source_id=source_id,
            title=str(exists[0].get("title") or source_id),
            weight=request.weight,
            chapter_weights=request.chapter_weights,
        )


mentor_service = MentorService()
