"""Tests for GUI book import (api/routers/books.py + commands/book_commands.py)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from commands.book_commands import (
    BookImportInput,
    converter_binary,
    import_book_command,
    validate_pdf_path,
)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


PDF_BYTES = b"%PDF-1.4\n%fake\n"


# --- endpoint ---------------------------------------------------------------


def test_import_rejects_non_pdf(client):
    response = client.post(
        "/api/books/import", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 400


def test_import_rejects_fake_pdf_content(client, tmp_path, monkeypatch):
    import api.routers.books as books_mod

    monkeypatch.setattr(books_mod, "UPLOAD_DIR", tmp_path)
    response = client.post(
        "/api/books/import",
        files={"file": ("book.pdf", b"not a pdf at all", "application/pdf")},
    )
    assert response.status_code == 400


def test_import_saves_pdf_and_submits_job(client, tmp_path, monkeypatch):
    import api.routers.books as books_mod

    monkeypatch.setattr(books_mod, "UPLOAD_DIR", tmp_path)
    with patch("surreal_commands.submit_command", return_value="command:job1") as mock_submit:
        response = client.post(
            "/api/books/import",
            files={"file": ("新しい本.pdf", PDF_BYTES, "application/pdf")},
            data={"title": "新しい本", "captions": "true"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "command:job1"
    assert body["status"] == "submitted"
    # PDF が保存され、ジョブにそのパスが渡っている
    args = mock_submit.call_args.args
    assert args[0] == "open_notebook" and args[1] == "import_book"
    saved = Path(args[2]["pdf_path"])
    assert saved.exists() and saved.read_bytes() == PDF_BYTES
    assert args[2]["title"] == "新しい本"


def test_safe_pdf_name_sanitizes():
    from api.routers.books import safe_pdf_name

    assert safe_pdf_name("コンサル頭のつくり方.pdf") == "コンサル頭のつくり方.pdf"
    assert safe_pdf_name("../etc/passwd") == "passwd.pdf"
    assert safe_pdf_name("a b/c!.pdf") == "c_.pdf"


# --- command job ------------------------------------------------------------


def test_validate_pdf_path_containment(tmp_path, monkeypatch):
    import commands.book_commands as bc

    monkeypatch.setattr(bc, "DATA_FOLDER", str(tmp_path / "data"))
    uploads = tmp_path / "data" / "uploads" / "books"
    uploads.mkdir(parents=True)
    good = uploads / "book.pdf"
    good.write_bytes(PDF_BYTES)
    assert validate_pdf_path(str(good)) == good.resolve()

    outside = tmp_path / "evil.pdf"
    outside.write_bytes(PDF_BYTES)
    with pytest.raises(ValueError, match="outside allowed"):
        validate_pdf_path(str(outside))
    with pytest.raises(ValueError, match="Not a PDF"):
        validate_pdf_path(str(uploads / "book.txt"))


@pytest.mark.asyncio
async def test_import_job_fails_clearly_without_converter(tmp_path, monkeypatch):
    """コンテナ worker（コンバータ無し）では明確な恒久エラーで failed になる。"""
    import commands.book_commands as bc

    monkeypatch.setattr(bc, "DATA_FOLDER", str(tmp_path / "data"))
    monkeypatch.setattr(bc, "SUPERBOOK_DIR", tmp_path / "no-converter")
    uploads = tmp_path / "data" / "uploads" / "books"
    uploads.mkdir(parents=True)
    pdf = uploads / "book.pdf"
    pdf.write_bytes(PDF_BYTES)

    with pytest.raises(ValueError, match="converter not found"):
        await import_book_command(BookImportInput(pdf_path=str(pdf)))


@pytest.mark.asyncio
async def test_import_job_converts_then_ingests(tmp_path, monkeypatch):
    import commands.book_commands as bc

    monkeypatch.setattr(bc, "DATA_FOLDER", str(tmp_path / "data"))
    uploads = tmp_path / "data" / "uploads" / "books"
    uploads.mkdir(parents=True)
    pdf = uploads / "本.pdf"
    pdf.write_bytes(PDF_BYTES)

    run_calls = {}

    async def fake_run_converter(pdf_path, out_dir):
        run_calls["pdf"] = pdf_path
        run_calls["out"] = out_dir

    ingest_mock = AsyncMock()
    monkeypatch.setattr(bc, "run_converter", fake_run_converter)
    with patch("scripts.ingest_book.ingest", new=ingest_mock):
        result = await import_book_command(
            BookImportInput(pdf_path=str(pdf), title="本のタイトル", captions=False)
        )

    assert result.success is True
    assert run_calls["out"] == Path(tmp_path / "data") / "books" / "本"
    kwargs = ingest_mock.await_args.kwargs
    assert kwargs["title"] == "本のタイトル"
    assert kwargs["captions"] is False


def test_converter_binary_path():
    assert converter_binary().name == "superbook-pdf"
