"""Book import job — GUI から「PDF を置くだけ」で 変換→取り込み を行う。

NotebookLM の「ブラウザにアップロードするだけ」に相当する体験を担う
surreal-commands ジョブ。API がアップロード PDF を保存して本ジョブを投げ、
worker（ホスト側、`make book-stack`）が:
  1. superbook-pdf（姉妹リポ、YomiToku OCR/MPS）で Markdown 変換
  2. scripts/ingest_book.ingest で Notebook/Source/図/埋め込みを投入
を実行する。進捗はフロントが /api/commands/jobs/{id} をポーリングする。

制約: 変換は MPS を使うためコンテナ内 worker では動かない（コンバータ未検出
なら明確なエラーで failed になる）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from loguru import logger
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.config import DATA_FOLDER

# 姉妹リポの場所（Makefile の SUPERBOOK と同じ既定）
SUPERBOOK_DIR = Path(
    os.getenv("SUPERBOOK_DIR", "../Rust_DN_SuperBook_PDF_Converter/superbook-pdf")
)
CONVERT_TIMEOUT_SECONDS = int(os.getenv("BOOK_CONVERT_TIMEOUT", str(3 * 60 * 60)))


class BookImportInput(CommandInput):
    pdf_path: str
    title: Optional[str] = None
    captions: bool = True
    caption_model: str = "claude-sonnet-5"


class BookImportOutput(CommandOutput):
    success: bool
    message: str
    out_dir: Optional[str] = None


def converter_binary() -> Path:
    return SUPERBOOK_DIR / "target" / "release" / "superbook-pdf"


def validate_pdf_path(raw: str) -> Path:
    """API が保存した PDF のみ受け付ける（uploads/input 配下への封じ込め）。"""
    pdf = Path(raw).resolve()
    allowed_roots = [
        (Path(DATA_FOLDER) / "uploads").resolve(),
        Path("input").resolve(),
    ]
    if pdf.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF: {pdf.name}")
    if not any(root in pdf.parents for root in allowed_roots):
        raise ValueError(f"PDF outside allowed folders: {pdf}")
    if not pdf.exists():
        raise ValueError(f"PDF not found: {pdf}")
    return pdf


async def run_converter(pdf: Path, out_dir: Path) -> None:
    binary = converter_binary()
    if not binary.exists():
        # ValueError -> 恒久失敗（リトライしない）。コンテナ worker はここに落ちる
        raise ValueError(
            f"superbook-pdf converter not found at {binary}. "
            "Run `make convert-book` once on the host to build it "
            "(or set SUPERBOOK_DIR). Note: conversion needs the host (MPS)."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        str(binary),
        "markdown",
        str(pdf),
        "-o",
        str(out_dir.resolve()),
        "--generate-metadata",
        "--gpu",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # YomiToku venv (ai_bridge/ai_venv) はコンバータの CWD 相対で発見される
        cwd=str(SUPERBOOK_DIR.resolve()),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), CONVERT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        raise ValueError(f"Conversion timed out after {CONVERT_TIMEOUT_SECONDS}s")
    tail = (stdout or b"")[-2000:].decode(errors="replace")
    if proc.returncode != 0:
        raise ValueError(f"superbook-pdf failed (exit {proc.returncode}): {tail}")
    logger.info(f"Converted {pdf.name} -> {out_dir} :: {tail[-300:]}")


@command("import_book", app="open_notebook", retry={"max_attempts": 1})
async def import_book_command(input_data: BookImportInput) -> BookImportOutput:
    """PDF → Markdown 変換 → 取り込み（キャプション+埋め込み）まで一気通貫。"""
    try:
        pdf = validate_pdf_path(input_data.pdf_path)
        out_dir = Path(DATA_FOLDER) / "books" / pdf.stem

        logger.info(f"Book import: converting {pdf.name} (this can take ~30min/400p)")
        await run_converter(pdf, out_dir)

        logger.info(f"Book import: ingesting {out_dir}")
        # scripts/ 直下は worker がリポルートで動く前提で import 可能
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts.ingest_book import ingest

        try:
            await ingest(
                out_dir=out_dir,
                pdf_path=pdf,
                title=input_data.title,
                captions=input_data.captions,
                caption_model=input_data.caption_model,
            )
        except SystemExit as e:  # ingest は CLI 流儀で sys.exit を使う
            raise ValueError(f"Ingest failed: {e}") from e

        return BookImportOutput(
            success=True,
            message=f"Imported {input_data.title or pdf.stem}",
            out_dir=str(out_dir),
        )
    except ValueError:
        raise  # 恒久失敗（surreal-commands が failed にする）
    except Exception as e:  # noqa: BLE001 - surface unexpected errors as failure
        logger.exception("Book import failed")
        raise ValueError(f"Book import failed: {e}") from e
