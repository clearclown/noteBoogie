"""Mentor (師匠) endpoints — Book Navigator fork (MENTOR_UI_DESIGN.md §3/§12)."""

from typing import List

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.mentor_service import (
    MentorConsultRequest,
    MentorConsultResponse,
    MentorMemoryResponse,
    MentorMessageResponse,
    MentorPersonaProfile,
    MentorPersonaResponse,
    MentorPersonaUpdateRequest,
    MentorWeightEntry,
    MentorWeightUpdateRequest,
    mentor_service,
)
from api.slide_review_service import SlideReviewResponse, slide_review_service

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


@router.get("/mentor/persona", response_model=MentorPersonaResponse)
async def get_mentor_persona():
    """アクティブな師匠ペルソナ（既定はコンサル。プリセット/自作に切替可）。"""
    return await mentor_service.get_persona()


@router.get("/mentor/personas", response_model=List[MentorPersonaProfile])
async def list_mentor_personas():
    """全ペルソナプロファイル（default=コンサル + generalist/engineer/editor/researcher + 自作）。"""
    return await mentor_service.list_personas()


@router.put("/mentor/personas/{name}", response_model=MentorPersonaProfile)
async def upsert_mentor_persona(name: str, request: MentorPersonaUpdateRequest):
    """プロファイルの本文を編集する（無ければ新規作成。切替は /activate）。"""
    return await mentor_service.upsert_persona(name, request)


@router.post("/mentor/personas/{name}/activate", response_model=MentorPersonaProfile)
async def activate_mentor_persona(name: str):
    """ペルソナを切り替える（相談・スライドレビューに即時反映）。"""
    return await mentor_service.activate_persona(name)


@router.post("/mentor/slide-review", response_model=SlideReviewResponse)
async def review_slides(file: UploadFile = File(...)):
    """スライド（png/jpg/pdf）を5軸ルーブリックでレビューし、品質ゲートを判定する。"""
    data = await file.read()
    return await slide_review_service.review(file.filename or "upload", data)


@router.get("/mentor/slide-reviews", response_model=List[SlideReviewResponse])
async def list_slide_reviews(limit: int = Query(default=20, ge=1, le=100)):
    """過去のスライドレビュー一覧（改善差分の確認用）。"""
    return await slide_review_service.list_reviews(limit)


class ApplyFixesRequest(BaseModel):
    issue_ids: List[str]


@router.post("/mentor/slide-review/{review_id}/apply")
async def apply_slide_fixes(review_id: str, request: ApplyFixesRequest):
    """選択した指摘を pptx に適用し、修正版（_coached.pptx）をダウンロードさせる。"""
    path = await slide_review_service.apply(review_id, request.issue_ids)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=path.name,
    )
