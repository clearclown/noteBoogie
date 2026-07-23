"""Tests for slide review (api/slide_review_service.py + mentor router §11)."""

import io
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.slide_review_service import (
    AXIS_KEYS,
    SlideIssue,
    build_axes,
    build_review_prompt,
    detect_kind,
    gate_verdict,
    parse_review_json,
    rasterize_pdf,
)
from open_notebook.exceptions import ExternalServiceError, InvalidInputError


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


def make_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def make_pdf(pages: int = 2) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 150)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


REVIEW_JSON = {
    "axes": [
        {"key": "logic", "score": 4.0, "issues": []},
        {"key": "message_body", "score": 3.5, "issues": []},
        {"key": "charts", "score": 2.0, "issues": [
            {"page": 2, "text": "円グラフが不適切", "fix": "横棒グラフに変更"},
        ]},
        {"key": "tone_manner", "score": 3.0, "issues": []},
        {"key": "design", "score": 3.2, "issues": []},
    ],
    "summary": "総評です",
    "key_messages": ["売上は伸びている"],
}


# --- unit ------------------------------------------------------------------


def test_detect_kind():
    assert detect_kind("deck.PDF") == "pdf"
    assert detect_kind("s.png") == "image"
    assert detect_kind("s.jpeg") == "image"
    assert detect_kind("deck.pptx") == "pptx"
    with pytest.raises(InvalidInputError):
        detect_kind("notes.txt")
    with pytest.raises(InvalidInputError):
        detect_kind("")


def test_parse_review_json_tolerates_fences_and_prose():
    text = "はい、レビューします。\n```json\n" + json.dumps(REVIEW_JSON) + "\n```\n以上です。"
    assert parse_review_json(text)["summary"] == "総評です"


def test_parse_review_json_rejects_garbage():
    with pytest.raises(ExternalServiceError):
        parse_review_json("JSONを出せませんでした")
    with pytest.raises(ExternalServiceError):
        parse_review_json("{broken json]")


def test_build_axes_fills_missing_axes_and_clamps():
    axes = build_axes(
        [{"key": "logic", "score": 9.0, "issues": [{"page": "x", "text": "指摘"}]}],
        threshold=3.0,
    )
    assert [a.key for a in axes] == AXIS_KEYS
    logic = axes[0]
    assert logic.score == 5.0  # clamped
    assert logic.issues[0].page == 1  # bad page -> 1
    # 欠けた軸は0点で未達
    assert all(a.score == 0.0 and not a.passed for a in axes[1:])


def test_build_axes_merges_extra_issues():
    extra = {"tone_manner": [SlideIssue(page=1, text="フォント3種", rule="font_count", applicable=True)]}
    axes = build_axes([], extra_issues=extra)
    tone = next(a for a in axes if a.key == "tone_manner")
    assert tone.issues[0].rule == "font_count"
    assert tone.issues[0].applicable is True


def test_gate_verdict():
    axes = build_axes(REVIEW_JSON["axes"], threshold=3.0)
    overall, passed, top_fix = gate_verdict(axes)
    assert overall == pytest.approx(3.14, abs=0.01)
    assert passed is False  # charts 2.0 < 3.0
    assert top_fix == "横棒グラフに変更"


def test_gate_verdict_passes_when_all_above_threshold():
    axes = build_axes(
        [{"key": k, "score": 4.0, "issues": []} for k in AXIS_KEYS], threshold=3.0
    )
    overall, passed, top_fix = gate_verdict(axes)
    assert (overall, passed, top_fix) == (4.0, True, None)


def test_rasterize_pdf_respects_page_cap():
    pdf = make_pdf(pages=3)
    pages = rasterize_pdf(pdf, max_pages=2)
    assert len(pages) == 2
    assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_rasterize_pdf_rejects_non_pdf():
    with pytest.raises(InvalidInputError):
        rasterize_pdf(b"not a pdf")


def test_review_prompt_mentions_all_axes():
    prompt = build_review_prompt(3)
    for key in AXIS_KEYS:
        assert key in prompt
    assert "3ページ" in prompt


# --- endpoint --------------------------------------------------------------


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_create", new_callable=AsyncMock)
async def test_slide_review_endpoint_png(mock_create, client):
    mock_create.return_value = {"id": "slide_review:r1"}
    with (
        patch(
            "api.slide_review_service.run_vision_review",
            new=AsyncMock(return_value=json.dumps(REVIEW_JSON)),
        ) as mock_vision,
        patch(
            "api.slide_review_service.ground_citations",
            new=AsyncMock(return_value=[]),
        ) as mock_ground,
    ):
        response = client.post(
            "/api/mentor/slide-review",
            files={"file": ("deck.png", make_png(), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "slide_review:r1"
    assert body["kind"] == "image"
    assert body["page_count"] == 1
    assert body["passed"] is False
    assert body["top_fix"] == "横棒グラフに変更"
    assert len(body["axes"]) == 5
    # grounding は summary + key_messages で検索する
    assert "売上は伸びている" in mock_ground.await_args.args[0]
    assert mock_vision.await_count == 1


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_create", new_callable=AsyncMock)
async def test_slide_review_endpoint_pdf_multipage(mock_create, client):
    mock_create.return_value = {"id": "slide_review:r2"}
    with (
        patch(
            "api.slide_review_service.run_vision_review",
            new=AsyncMock(return_value=json.dumps(REVIEW_JSON)),
        ) as mock_vision,
        patch(
            "api.slide_review_service.ground_citations",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = client.post(
            "/api/mentor/slide-review",
            files={"file": ("deck.pdf", make_pdf(2), "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["page_count"] == 2
    # 2ページ分の画像が vision に渡る
    assert len(mock_vision.await_args.args[0]) == 2


def test_slide_review_endpoint_rejects_unknown_type(client):
    response = client.post(
        "/api/mentor/slide-review",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@patch("open_notebook.database.repository.repo_query", new_callable=AsyncMock)
async def test_list_slide_reviews(mock_query, client):
    mock_query.return_value = [
        {
            "id": "slide_review:r1",
            "filename": "deck.png",
            "kind": "image",
            "page_count": 1,
            "overall": 3.14,
            "passed": False,
            "axes": [a.model_dump() for a in build_axes(REVIEW_JSON["axes"])],
            "summary": "総評です",
            "citations": [{"id": "source:a", "title": "本A"}],
            "created": "t1",
        }
    ]
    response = client.get("/api/mentor/slide-reviews")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["top_fix"] == "横棒グラフに変更"
    assert body[0]["citations"][0]["title"] == "本A"
