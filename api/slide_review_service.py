"""Slide review service — mentor coach for decks (MENTOR_UI_DESIGN.md §11).

Image/PDF uploads are rasterized (pypdfium2) and reviewed by the default
chat model (vision) against a 5-axis consulting rubric. Findings are
grounded in the library (weighted vector search, same weights as the
mentor's recall) and gated on a minimum quality threshold — the score is
not advice, it is a gate: below-threshold axes are flagged with the one
fix to make first.
"""

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from open_notebook.exceptions import (
    ExternalServiceError,
    InvalidInputError,
)

SLIDE_REVIEW_DIR = Path(os.getenv("DATA_FOLDER", "./data")) / "slide_reviews"
GATE_THRESHOLD = float(os.getenv("SLIDE_GATE_THRESHOLD", "3.0"))
# vision コスト暴走ガード（1回のレビューで見るページ数上限）
MAX_REVIEW_PAGES = int(os.getenv("SLIDE_REVIEW_MAX_PAGES", "10"))
RASTER_SCALE = 2.0  # 72dpi × 2 = 144dpi 相当（文字が読める最小限）

IMAGE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# 5軸ルーブリック（キーは安定・表示名はフロントの i18n が持つ）
AXIS_KEYS = ["logic", "message_body", "charts", "tone_manner", "design"]
AXIS_NAMES_JA = {
    "logic": "論理整理・論点整理",
    "message_body": "メッセージ×ボディ整合",
    "charts": "表・グラフの整理",
    "tone_manner": "トンマナ",
    "design": "デザイン",
}


class SlideIssue(BaseModel):
    page: int = 1
    text: str
    fix: Optional[str] = None
    # C4: pptx 由来の決定的 lint は適用可能（apply エンドポイントの対象）
    rule: Optional[str] = None
    applicable: bool = False


class SlideAxis(BaseModel):
    key: str
    score: float = Field(ge=0.0, le=5.0)
    issues: List[SlideIssue] = Field(default_factory=list)
    passed: bool = True


class SlideCitation(BaseModel):
    id: str
    title: str


class SlideReviewResponse(BaseModel):
    id: Optional[str] = None
    filename: str
    kind: str
    page_count: int
    overall: float
    passed: bool
    threshold: float
    axes: List[SlideAxis]
    summary: Optional[str] = None
    top_fix: Optional[str] = None
    citations: List[SlideCitation] = Field(default_factory=list)
    created: Optional[str] = None


def detect_kind(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".pptx":
        return "pptx"
    raise InvalidInputError(
        f"Unsupported file type: {suffix or '(none)'} (png/jpg/pdf/pptx)"
    )


def rasterize_pdf(data: bytes, max_pages: int = MAX_REVIEW_PAGES) -> List[bytes]:
    """PDF をページ毎の PNG bytes に変換する（pypdfium2、上限あり）。"""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(data)
    except Exception as e:
        raise InvalidInputError(f"Could not open PDF: {e}") from e
    try:
        pages: List[bytes] = []
        for index in range(min(len(doc), max_pages)):
            bitmap = doc[index].render(scale=RASTER_SCALE)
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pages.append(buffer.getvalue())
        return pages
    finally:
        doc.close()


def build_review_prompt(page_count: int) -> str:
    axis_lines = "\n".join(
        f"- {key}: {AXIS_NAMES_JA[key]}" for key in AXIS_KEYS
    )
    return f"""あなたは戦略コンサルタントの「師匠」として、弟子のスライド（{page_count}ページ）をレビューします。

以下の5軸で 0.0〜5.0 の採点と、具体的な指摘（該当ページ番号つき）・書き直し例を出してください:
{axis_lines}

採点の目安: 5=クライアント提出可 / 3=社内レビュー通過ライン / 1=根本的に作り直し。
指摘は軸ごとに最大3件、最も直すべきものから。fix には修正後の文言・構成の具体例を書く。

次の JSON だけを出力してください（前後に文章を付けない）:
{{"axes": [{{"key": "logic", "score": 3.5, "issues": [{{"page": 1, "text": "指摘", "fix": "書き直し例"}}]}}, ...5軸すべて...],
 "summary": "総評（結論から、良い点→最優先の直し）",
 "key_messages": ["スライドの主要メッセージを1行ずつ"]}}"""


def parse_review_json(text: str) -> Dict[str, Any]:
    """LLM 応答から JSON を取り出す（フェンス・前後の文章に耐える）。"""
    cleaned = re.sub(r"```(?:json)?", "", text).strip("` \n")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ExternalServiceError("Slide review model returned no JSON")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise ExternalServiceError(f"Slide review JSON invalid: {e}") from e


def build_axes(
    raw_axes: List[Dict[str, Any]],
    threshold: float = GATE_THRESHOLD,
    extra_issues: Optional[Dict[str, List[SlideIssue]]] = None,
) -> List[SlideAxis]:
    """LLM の軸採点を正規化し、欠けた軸は 0 点扱いで必ず5軸返す。"""
    by_key = {str(a.get("key", "")): a for a in raw_axes or []}
    axes: List[SlideAxis] = []
    for key in AXIS_KEYS:
        raw = by_key.get(key, {})
        try:
            score = max(0.0, min(5.0, float(raw.get("score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        issues = []
        for issue in raw.get("issues") or []:
            if not isinstance(issue, dict) or not issue.get("text"):
                continue
            try:
                page = int(issue.get("page", 1))
            except (TypeError, ValueError):
                page = 1
            issues.append(
                SlideIssue(page=page, text=str(issue["text"]), fix=issue.get("fix"))
            )
        issues.extend((extra_issues or {}).get(key, []))
        axes.append(
            SlideAxis(key=key, score=score, issues=issues, passed=score >= threshold)
        )
    return axes


def gate_verdict(axes: List[SlideAxis]) -> tuple[float, bool, Optional[str]]:
    """総合点・ゲート判定・最優先の直し（最低軸の先頭指摘）を返す。"""
    if not axes:
        return 0.0, False, None
    overall = round(sum(a.score for a in axes) / len(axes), 2)
    passed = all(a.passed for a in axes)
    top_fix: Optional[str] = None
    if not passed:
        worst = min((a for a in axes if not a.passed), key=lambda a: a.score)
        if worst.issues:
            top_fix = worst.issues[0].fix or worst.issues[0].text
    return overall, passed, top_fix


async def ground_citations(query: str, limit: int = 3) -> List[SlideCitation]:
    """主要メッセージで蔵書を検索し、mentor と同じ傾斜で引用本を選ぶ。"""
    from api.mentor_service import extract_source_refs
    from open_notebook.domain.notebook import vector_search
    from open_notebook.graphs.mentor import (
        apply_weights,
        compute_auto_factors,
        load_manual_weights,
    )

    if not query.strip():
        return []
    try:
        hits = await vector_search(query, limit * 4, source=True, note=False)
        try:
            manual = await load_manual_weights()
        except Exception:  # noqa: BLE001 - weights are best-effort
            manual = {}
        weighted = apply_weights(hits, manual, compute_auto_factors([]))
        refs = extract_source_refs(weighted)[:limit]
        return [SlideCitation(id=r.id, title=r.title) for r in refs]
    except Exception as e:  # noqa: BLE001 - grounding is best-effort
        logger.warning(f"slide review grounding failed: {e}")
        return []


async def run_vision_review(images: List[tuple[bytes, str]], prompt: str) -> str:
    """画像列 + ルーブリックを default chat model（vision）に投げる。"""
    from langchain_core.messages import HumanMessage

    from open_notebook.ai.provision import provision_langchain_model

    content: List[Dict[str, Any]] = []
    for data, media_type in images:
        encoded = base64.b64encode(data).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}}
        )
    content.append({"type": "text", "text": prompt})

    model = await provision_langchain_model(prompt, None, "chat", max_tokens=4000)
    response = await model.ainvoke([HumanMessage(content=content)])  # type: ignore[arg-type]

    from open_notebook.utils import clean_thinking_content
    from open_notebook.utils.text_utils import extract_text_content

    return clean_thinking_content(extract_text_content(response.content))


class SlideReviewService:
    @staticmethod
    async def review(filename: str, data: bytes) -> SlideReviewResponse:
        kind = detect_kind(filename)
        if not data:
            raise InvalidInputError("Uploaded file is empty")

        stored_path: Optional[str] = None
        extra_issues: Dict[str, List[SlideIssue]] = {}
        if kind == "image":
            media_type = IMAGE_EXTENSIONS[Path(filename).suffix.lower()]
            images = [(data, media_type)]
        elif kind == "pdf":
            pages = rasterize_pdf(data)
            if not pages:
                raise InvalidInputError("PDF has no pages")
            images = [(page, "image/png") for page in pages]
        else:  # pptx — C4 で構造解析を追加
            raise InvalidInputError("pptx review is not available yet")

        prompt = build_review_prompt(len(images))
        raw_text = await run_vision_review(images, prompt)
        parsed = parse_review_json(raw_text)

        axes = build_axes(parsed.get("axes") or [], extra_issues=extra_issues)
        overall, passed, top_fix = gate_verdict(axes)
        summary = str(parsed.get("summary") or "") or None
        key_messages = [str(m) for m in parsed.get("key_messages") or []]
        citations = await ground_citations(" ".join([summary or "", *key_messages]))

        review = SlideReviewResponse(
            filename=filename,
            kind=kind,
            page_count=len(images),
            overall=overall,
            passed=passed,
            threshold=GATE_THRESHOLD,
            axes=axes,
            summary=summary,
            top_fix=top_fix,
            citations=citations,
        )

        try:
            from open_notebook.database.repository import repo_create

            row = await repo_create(
                "slide_review",
                {
                    "filename": filename,
                    "kind": kind,
                    "page_count": review.page_count,
                    "overall": overall,
                    "passed": passed,
                    "axes": [a.model_dump() for a in axes],
                    "summary": summary,
                    "citations": [c.model_dump() for c in citations],
                    "stored_path": stored_path,
                },
            )
            raw_id = row.get("id") if isinstance(row, dict) else None
            review.id = str(raw_id) if raw_id else None
        except Exception as e:  # noqa: BLE001 - persistence is best-effort
            logger.warning(f"slide review persistence failed: {e}")

        return review

    @staticmethod
    async def list_reviews(limit: int = 20) -> List[SlideReviewResponse]:
        from open_notebook.database.repository import repo_query

        rows = await repo_query(
            "SELECT type::string(id) AS id, filename, kind, page_count, overall, "
            "passed, axes, summary, citations, type::string(created) AS created "
            "FROM slide_review ORDER BY created DESC LIMIT $n",
            {"n": limit},
        )
        reviews = []
        for row in rows:
            axes = [SlideAxis(**a) for a in row.get("axes") or []]
            _, _, top_fix = gate_verdict(axes)
            reviews.append(
                SlideReviewResponse(
                    id=row["id"],
                    filename=row.get("filename") or "",
                    kind=row.get("kind") or "image",
                    page_count=int(row.get("page_count") or 1),
                    overall=float(row.get("overall") or 0.0),
                    passed=bool(row.get("passed")),
                    threshold=GATE_THRESHOLD,
                    axes=axes,
                    summary=row.get("summary"),
                    top_fix=top_fix,
                    citations=[SlideCitation(**c) for c in row.get("citations") or []],
                    created=row.get("created"),
                )
            )
        return reviews


slide_review_service = SlideReviewService()
