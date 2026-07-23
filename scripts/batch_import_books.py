"""複数zipの一括取り込みバッチ — ダウンロード監視→展開→並列 変換/取り込み→全章mp3生成.

想定: ~/Downloads にコンサル/経営系の zip（各内に複数PDF）が落ちてくる。
本スクリプトはダウンロード完了（.crdownload 消滅 + サイズ安定）を待ち、
  1. zip を data/uploads/books_batch/ へ展開し PDF を列挙
  2. superbook-pdf 変換（並列2・MPS）→ ingest（並列2・vision キャプション+埋め込み）
  3. gateway へオーディオブック生成を投入（同時3冊、章は各冊内で逐次）し完走を監視
  4. メンターのペルソナを「戦略コンサル×経営戦略の師」に更新
を全自動で行う。ログは標準出力（起動側でファイルへリダイレクト）。

Usage:
    uv run --env-file .env python scripts/batch_import_books.py \
        [--expect-zips 3] [--no-audiobooks] [--downloads ~/Downloads]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# コンテナスタック（docker-compose.book.yml）の公開ポートに合わせる
os.environ.setdefault("SURREAL_URL", "ws://localhost:8000/rpc")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8088")
API_URL = os.getenv("API_URL", "http://localhost:5055")

CONVERTER = Path(
    os.getenv("SUPERBOOK_DIR", "../Rust_DN_SuperBook_PDF_Converter/superbook-pdf")
) / "target" / "release" / "superbook-pdf"
BATCH_DIR = Path("data/uploads/books_batch")
BOOKS_DIR = Path("data/books")
REPORT_PATH = Path("data/batch_import_report.json")

ZIP_NAME_RE = re.compile(r"(コンサル|経営)")
CONVERT_PARALLEL = 2   # MPS メモリの都合で2並列まで
INGEST_PARALLEL = 2
GENERATE_PARALLEL = 3  # 同時生成する本の数（章は各本内で逐次）

MENTOR_PERSONA = (
    "あなたは戦略コンサルティングと経営戦略の両方を極めた「師匠」です。"
    "外資系戦略ファームでの実務と経営者への助言経験を持ち、蔵書（コンサル・経営戦略の書籍群）を"
    "深く読み込んでいます。相談者（弟子）のキャリアと仕事の質、そして経営者としての意思決定力を"
    "引き上げることに責任を持っています。"
)


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# 1. ダウンロード完了待ち
# ---------------------------------------------------------------------------


def matching_zips(downloads: Path, since: float) -> list[Path]:
    return sorted(
        p for p in downloads.glob("*.zip")
        if ZIP_NAME_RE.search(p.name) and p.stat().st_mtime >= since
    )


def wait_for_downloads(downloads: Path, expect: int, since: float) -> list[Path]:
    """crdownload が消え、対象zipが expect 個そろい、サイズが安定するまで待つ。"""
    log(f"ダウンロード監視開始: {downloads}（対象 {expect} 個、コンサル/経営 の zip）")
    last_sizes: dict[str, int] = {}
    deadline = time.time() + 6 * 3600
    while time.time() < deadline:
        partial = list(downloads.glob("*.crdownload"))
        zips = matching_zips(downloads, since)
        sizes = {p.name: p.stat().st_size for p in zips}
        if not partial and len(zips) >= expect and sizes == last_sizes:
            log(f"ダウンロード完了を検知: {[p.name for p in zips]}")
            return zips
        state = f"zip {len(zips)}/{expect}, ダウンロード中 {len(partial)} 件"
        if partial:
            done = sum(p.stat().st_size for p in partial) / 1e9
            state += f"（部分 {done:.2f}GB）"
        log(f"待機中… {state}")
        last_sizes = sizes
        time.sleep(60)
    raise TimeoutError("ダウンロードが6時間以内に完了しませんでした")


# ---------------------------------------------------------------------------
# 2. 展開と PDF 列挙
# ---------------------------------------------------------------------------


def extract_zips(zips: list[Path]) -> list[Path]:
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    pdfs: list[Path] = []
    for zf in zips:
        dest = BATCH_DIR / re.sub(r"[^\w一-龠ぁ-んァ-ヶー-]", "_", zf.stem)[:60]
        log(f"展開: {zf.name} -> {dest}")
        # ditto は日本語ファイル名の zip に強い（macOS 標準）
        subprocess.run(["ditto", "-x", "-k", str(zf), str(dest)], check=True)
        for pdf in dest.rglob("*.pdf"):
            if "__MACOSX" in pdf.parts or pdf.stat().st_size < 1_000_000:
                continue
            pdfs.append(pdf)
    # 同名（同タイトル）の重複を除去
    seen: set[str] = set()
    unique = []
    for pdf in sorted(pdfs):
        if pdf.stem not in seen:
            seen.add(pdf.stem)
            unique.append(pdf)
    log(f"PDF {len(unique)} 冊を検出: {[p.stem for p in unique]}")
    return unique


def book_title(pdf: Path) -> str:
    """Takeout 等の付帯情報を落としたタイトル。"""
    return re.sub(r"\s*[-_]?\d{8,}.*$", "", pdf.stem).strip() or pdf.stem


def safe_dir_name(title: str) -> str:
    return re.sub(r"[^\w一-龠ぁ-んァ-ヶー-]", "_", title)[:80]


# ---------------------------------------------------------------------------
# 3. 変換 / 取り込み / 生成
# ---------------------------------------------------------------------------


async def wait_for_stack() -> None:
    import httpx

    log("スタック（DB/API/gateway）の起動を待機…")
    deadline = time.time() + 4 * 3600  # 初回はイメージビルドが長い（夜間運転前提）
    async with httpx.AsyncClient(timeout=5) as client:
        while time.time() < deadline:
            try:
                api = await client.get(f"{API_URL}/health")
                gw = await client.get(f"{GATEWAY_URL}/health")
                if api.status_code == 200 and gw.status_code == 200:
                    log("スタック起動確認")
                    return
            except Exception:
                pass
            await asyncio.sleep(20)
    raise TimeoutError("スタックが45分以内に起動しませんでした")


async def convert_pdf(pdf: Path, out_dir: Path, sem: asyncio.Semaphore) -> bool:
    async with sem:
        if (out_dir / "book_manifest.json").exists():
            log(f"変換スキップ（既存）: {pdf.stem}")
            return True
        log(f"変換開始: {pdf.stem}")
        proc = await asyncio.create_subprocess_exec(
            str(CONVERTER), "markdown", str(pdf.resolve()), "-o", str(out_dir.resolve()),
            "--generate-metadata", "--gpu",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            # YomiToku venv (ai_bridge/ai_venv) はコンバータの CWD 相対で発見される。
            # CWD を合わせないと無OCRフォールバックで全ページ画像の md が出る（実測）
            cwd=str(CONVERTER.parent.parent.parent),
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            log(f"変換失敗: {pdf.stem}: {(out or b'')[-500:].decode(errors='replace')}")
            return False
        log(f"変換完了: {pdf.stem}")
        return True


async def existing_titles() -> set[str]:
    from open_notebook.database.repository import repo_query

    rows = await repo_query("SELECT title FROM source")
    return {str(r.get("title") or "") for r in rows}


async def ingest_book_dir(
    out_dir: Path, pdf: Path, title: str, sem: asyncio.Semaphore
) -> str | None:
    """取り込み → source id を返す（失敗は None）。"""
    from open_notebook.database.repository import repo_query
    from scripts.ingest_book import ingest

    async with sem:
        log(f"取り込み開始: {title}")
        try:
            await ingest(
                out_dir=out_dir, pdf_path=pdf, title=title,
                captions=True, caption_model="claude-sonnet-5",
            )
        except SystemExit as e:
            log(f"取り込み失敗: {title}: {e}")
            return None
        except Exception as e:  # noqa: BLE001
            log(f"取り込み失敗: {title}: {e}")
            return None
        rows = await repo_query(
            "SELECT type::string(id) AS id FROM source WHERE title = $t", {"t": title}
        )
        source_id = rows[0]["id"] if rows else None
        log(f"取り込み完了: {title} ({source_id})")
        return source_id


async def generate_audiobook(title: str, source_id: str, sem: asyncio.Semaphore) -> dict:
    """gateway に生成を投入し、全章の完了/失敗まで監視する。"""
    import httpx

    async with sem:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{GATEWAY_URL}/audiobooks/generate",
                json={"audiobook_name": title, "source_id": source_id},
            )
            if resp.status_code != 201:
                log(f"生成投入失敗: {title}: {resp.status_code} {resp.text[:200]}")
                return {"title": title, "status": "submit_failed"}
            audiobook_id = resp.json()["audiobook_id"]
            chapter_count = resp.json().get("chapter_count")
            log(f"生成開始: {title} ({chapter_count}章, {audiobook_id})")

            deadline = time.time() + 8 * 3600
            while time.time() < deadline:
                await asyncio.sleep(60)
                detail = (
                    await client.get(
                        f"{GATEWAY_URL}/audiobooks/{audiobook_id.replace(':', '%3A')}"
                    )
                ).json()
                chapters = detail.get("chapters") or []
                done = sum(1 for c in chapters if c.get("audio_file"))
                failed = sum(1 for c in chapters if c.get("generation_error"))
                if done + failed >= len(chapters) and chapters:
                    log(f"生成完了: {title} 成功{done}/失敗{failed}/全{len(chapters)}章")
                    return {
                        "title": title, "audiobook_id": audiobook_id,
                        "status": "done", "chapters": len(chapters),
                        "succeeded": done, "failed": failed,
                    }
                log(f"生成中: {title} {done+failed}/{len(chapters)}章")
            return {"title": title, "audiobook_id": audiobook_id, "status": "timeout"}


async def set_mentor_persona() -> None:
    from open_notebook.database.repository import repo_query

    await repo_query(
        "UPDATE mentor_profile SET persona = $p, active = (name = 'default') "
        "WHERE name = 'default'; "
        "UPDATE mentor_profile SET active = false WHERE name != 'default'",
        {"p": MENTOR_PERSONA},
    )
    log("メンターのペルソナを『戦略コンサル×経営戦略の師』に更新（active）")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--downloads", default=str(Path.home() / "Downloads"))
    ap.add_argument("--expect-zips", type=int, default=3)
    ap.add_argument("--since-hours", type=float, default=12.0,
                    help="この時間以内に更新された zip のみ対象")
    ap.add_argument("--no-audiobooks", action="store_true")
    args = ap.parse_args()

    since = time.time() - args.since_hours * 3600
    zips = await asyncio.to_thread(
        wait_for_downloads, Path(args.downloads).expanduser(), args.expect_zips, since
    )
    pdfs = extract_zips(zips)
    if not pdfs:
        log("PDF が見つかりませんでした")
        return

    await wait_for_stack()
    await set_mentor_persona()

    done_titles = await existing_titles()
    targets = []
    for pdf in pdfs:
        title = book_title(pdf)
        if title in done_titles:
            log(f"スキップ（取り込み済み）: {title}")
            continue
        targets.append((pdf, title, BOOKS_DIR / safe_dir_name(title)))

    # 本ごとの連続パイプライン: 変換(GPU)→取り込み(vision/埋め込みAPI)→生成(LLM/TTS API)。
    # フェーズを跨いで資源が重なるため、フェーズ直列より数時間速い。
    conv_sem = asyncio.Semaphore(CONVERT_PARALLEL)
    ing_sem = asyncio.Semaphore(INGEST_PARALLEL)
    gen_sem = asyncio.Semaphore(GENERATE_PARALLEL)

    async def book_pipeline(pdf: Path, title: str, out_dir: Path) -> dict:
        if not await convert_pdf(pdf, out_dir, conv_sem):
            return {"title": title, "status": "convert_failed"}
        source_id = await ingest_book_dir(out_dir, pdf, title, ing_sem)
        if not source_id:
            return {"title": title, "status": "ingest_failed"}
        if args.no_audiobooks:
            return {"title": title, "status": "ingested", "source_id": source_id}
        return await generate_audiobook(title, source_id, gen_sem)

    results = await asyncio.gather(
        *(book_pipeline(pdf, title, out_dir) for pdf, title, out_dir in targets)
    )

    report: dict = {
        "zips": [z.name for z in zips],
        "pdfs": len(pdfs),
        "skipped_existing": len(pdfs) - len(targets),
        "results": list(results),
        "generation_done": sum(1 for r in results if r.get("status") == "done"),
        "failed": [r for r in results if "failed" in str(r.get("status"))],
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log(f"バッチ完了。レポート: {REPORT_PATH}")
    log(json.dumps(report, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    asyncio.run(main())
