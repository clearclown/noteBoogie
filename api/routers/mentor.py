"""Mentor (師匠) endpoints — Book Navigator fork (MENTOR_UI_DESIGN.md §3/§12)."""

from typing import List

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from api.mentor_service import (
    MentorConsultRequest,
    MentorConsultResponse,
    MentorMemoryResponse,
    MentorMessageResponse,
    MentorWeightEntry,
    MentorWeightUpdateRequest,
    mentor_service,
)

router = APIRouter()


@router.post("/mentor/consult", response_model=MentorConsultResponse)
async def consult_mentor(request: MentorConsultRequest):
    """蔵書+長期記憶を持つ師匠に相談する（mentor グラフを実行）。"""
    return await mentor_service.consult(request.message)


@router.post("/mentor/speak/{message_id}")
async def speak_mentor_message(message_id: str):
    """師匠回答をTTSで音声化して mp3 を返す（キャッシュあり）。"""
    path = await mentor_service.speak(message_id)
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@router.get("/mentor/messages", response_model=List[MentorMessageResponse])
async def list_mentor_messages(limit: int = Query(default=50, ge=1, le=200)):
    """表示用の会話ログ（古い→新しい順）。"""
    return await mentor_service.list_messages(limit)


@router.get("/mentor/memories", response_model=List[MentorMemoryResponse])
async def list_mentor_memories(limit: int = Query(default=20, ge=1, le=100)):
    """長期記憶（mentor_memory）の一覧。"""
    return await mentor_service.list_memories(limit)


@router.delete("/mentor/memories/{memory_id}")
async def delete_mentor_memory(memory_id: str):
    """誤学習した記憶を手動で削除する。"""
    await mentor_service.delete_memory(memory_id)
    return {"message": "Memory deleted"}


@router.get("/mentor/weights", response_model=List[MentorWeightEntry])
async def get_mentor_weights():
    """全蔵書の学習傾斜（手動×自動）と章リスト。"""
    return await mentor_service.get_weights()


@router.put("/mentor/weights/{source_id}", response_model=MentorWeightEntry)
async def update_mentor_weight(source_id: str, request: MentorWeightUpdateRequest):
    """本単位の手動傾斜（0.0〜2.0）と章単位の微調整を保存する。"""
    return await mentor_service.update_weight(source_id, request)
