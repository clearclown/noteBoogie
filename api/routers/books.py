"""Book import endpoints — GUI から PDF を投げるだけで蔵書に入る（Book Navigator）。

POST /api/books/import が PDF を保存して import_book ジョブ（変換→取り込み）を
投げ、フロントは既存の /api/commands/jobs/{job_id} で進捗をポーリングする。
"""

import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel

from open_notebook.config import DATA_FOLDER
from open_notebook.exceptions import OpenNotebookError

router = APIRouter()

UPLOAD_DIR = Path(DATA_FOLDER) / "uploads" / "books"


class BookImportResponse(BaseModel):
    job_id: str
    status: str
    pdf_name: str
    title: Optional[str] = None


def safe_pdf_name(filename: str) -> str:
    stem = Path(filename or "book").stem
    safe = re.sub(r"[^\w\-一-龠ぁ-んァ-ヶー]", "_", stem)[:80] or "book"
    return f"{safe}.pdf"


@router.post("/books/import", response_model=BookImportResponse)
async def import_book(
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    captions: bool = Form(default=True),
):
    """PDF をアップロードして 変換→取り込み ジョブを開始する。

    変換（YomiToku/MPS）はホスト側 worker が実行する。所要 ~30分/400頁。
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF ファイルのみ受け付けます")

    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        pdf_name = safe_pdf_name(file.filename or "book")
        pdf_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{pdf_name}"
        data = await file.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="PDF として読めないファイルです")
        pdf_path.write_bytes(data)

        # submit_command はローカルレジストリを検証するため先に import する
        try:
            import commands.book_commands  # noqa: F401
        except ImportError as import_err:
            logger.error(f"Failed to import book commands: {import_err}")
            raise HTTPException(status_code=500, detail="Book import job unavailable")

        from surreal_commands import submit_command

        job_id = submit_command(
            "open_notebook",
            "import_book",
            {
                "pdf_path": str(pdf_path),
                "title": title,
                "captions": captions,
            },
        )
        if not job_id:
            raise HTTPException(status_code=500, detail="Failed to submit import job")

        logger.info(f"Book import submitted: {job_id} ({pdf_name})")
        return BookImportResponse(
            job_id=str(job_id), status="submitted", pdf_name=pdf_name, title=title
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error importing book: {e}")
        raise HTTPException(status_code=500, detail="Book import failed")
