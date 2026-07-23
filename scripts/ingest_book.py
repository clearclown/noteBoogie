"""Ingest a SuperBook-converted book into Open Notebook, the native way.

Input: a `superbook-pdf markdown` output directory (merged .md + images/ +
book_manifest.json). Produces:

  1. A Notebook named after the book, and a Source with the full Markdown
     (linked via the `reference` edge) — the pattern Open Notebook's own
     ingestion uses in-process, since no raw-text HTTP endpoint exists.
  2. `book_figure` records (migration 17) for every separated figure, with
     Claude-vision captions for kind=="figure" images.
  3. Figure image links in the Markdown replaced by 【図: <caption>】 markers
     so the audiobook script narrates figures; remaining image links stripped.
  4. An embedding job via `source.vectorize()` (requires the surreal-commands
     worker and a configured default embedding model to actually run).

Usage:
    uv run --env-file .env python scripts/ingest_book.py \
        --dir /path/to/markdown_output --pdf input/book.pdf [--title 本の題名] \
        [--no-captions] [--caption-model claude-sonnet-5]
"""

import argparse
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

from loguru import logger

from open_notebook.database.repository import ensure_record_id, repo_insert, repo_query
from open_notebook.domain.notebook import Asset, Notebook, Source

CAPTION_PROMPT = (
    "これは書籍からスキャン抽出された図表です。耳だけで聴く読者のために、"
    "この図が何を示しているかを日本語で2〜3文、簡潔に説明してください。"
    "軸・分類・矢印など構造があれば言葉で描写してください。"
    "前置きなしで説明文のみを返してください。"
)


def caption_figure(client, model: str, image_path: Path) -> "str | None":
    """Caption one figure image with Claude vision. Returns None on failure."""
    try:
        data = base64.standard_b64encode(image_path.read_bytes()).decode()
        suffix = image_path.suffix.lower().lstrip(".")
        media_type = f"image/{'jpeg' if suffix in ('jpg', 'jpeg') else suffix}"
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": data,
                            },
                        },
                        {"type": "text", "text": CAPTION_PROMPT},
                    ],
                }
            ],
        )
        if response.stop_reason == "refusal":
            return None
        return next(
            (b.text.strip() for b in response.content if b.type == "text"), None
        )
    except Exception as e:  # noqa: BLE001 - per-figure best effort
        logger.warning(f"caption failed for {image_path.name}: {e}")
        return None


def rewrite_markdown_for_audio(md: str, figure_captions: dict) -> str:
    """Replace image links with 【図: caption】 markers (or strip uncaptioned).

    The audiobook narrator can't show an image; a captioned marker lets the
    script briefing turn it into a spoken description instead.
    """

    def replace_image_link(match: re.Match) -> str:
        target = match.group(1).strip()
        cap = figure_captions.get(target)
        return f"【図: {cap}】" if cap else ""

    # `![alt](images/...)` -> caption marker (or stripped when uncaptioned)
    return re.sub(r"!\[[^\]]*\]\(([^)]+)\)\n?", replace_image_link, md)


def chapter_index_for_page(chapters: list, page: int) -> "int | None":
    """Map a 1-based page number to the 0-based index of its chapter."""
    idx = None
    for i, ch in enumerate(chapters):
        if ch["page"] <= page:
            idx = i
        else:
            break
    return idx


async def ingest(
    out_dir: Path,
    pdf_path: "Path | None",
    title: "str | None",
    captions: bool,
    caption_model: str,
) -> None:
    manifest_path = out_dir / "book_manifest.json"
    if not manifest_path.exists():
        sys.exit(f"book_manifest.json not found in {out_dir} (run with --generate-metadata)")
    manifest = json.loads(manifest_path.read_text())

    md_files = [p for p in out_dir.glob("*.md")]
    if not md_files:
        sys.exit(f"no merged .md found in {out_dir}")
    md_path = md_files[0]
    md = md_path.read_text()
    book_title = title or md_path.stem

    # --- 1. Caption figures and rewrite the Markdown for audio ---
    figure_captions: dict[str, "str | None"] = {}
    figures = manifest.get("figures", [])
    # full_page = ページ全体が画像（扉・写真・全面図）。covers は装丁なので除外。
    to_caption = (
        [f for f in figures if f["kind"] in ("figure", "full_page")] if captions else []
    )
    if to_caption:
        import anthropic

        client = anthropic.Anthropic()
        logger.info(f"captioning {len(to_caption)} figures with {caption_model}...")
        for i, fig in enumerate(to_caption, 1):
            img = out_dir / fig["path"]
            if not img.exists():
                continue
            cap = caption_figure(client, caption_model, img)
            figure_captions[fig["path"]] = cap
            logger.info(f"  [{i}/{len(to_caption)}] {fig['path']}: "
                        f"{(cap or 'no caption')[:60]}")

    md_for_audio = rewrite_markdown_for_audio(md, figure_captions)

    # --- 2. Notebook + Source (in-process; no raw-text HTTP API exists) ---
    notebook = Notebook(name=book_title, description=f"書籍『{book_title}』の取り込み")
    await notebook.save()

    source = Source(
        title=book_title,
        full_text=md_for_audio,
        asset=Asset(file_path=str(pdf_path) if pdf_path else str(md_path)),
    )
    await source.save()
    await source.add_to_notebook(str(notebook.id))

    # --- 3. book_figure records ---
    chapters = manifest.get("chapters", [])
    records = []
    for fig in figures:
        records.append(
            {
                "source": ensure_record_id(str(source.id)),
                "page": fig["page"],
                "chapter_index": chapter_index_for_page(chapters, fig["page"]),
                "path": str((out_dir / fig["path"]).resolve()),
                "kind": fig["kind"],
                "caption": figure_captions.get(fig["path"]),
            }
        )
    if records:
        await repo_insert("book_figure", records)

    # --- 4. Embedding (async job; needs worker + default embedding model) ---
    # The `commands` package lives at the repo root and is not installed in the
    # venv; the embed_source command only exists once its module is imported.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import commands.embedding_commands  # noqa: F401  # registers embed_source

    command_id = await source.vectorize()

    print("\n=== ingest complete ===")
    print(f"notebook: {notebook.id}  ({book_title})")
    print(f"source:   {source.id}  ({len(md_for_audio):,} chars)")
    print(f"chapters: {len(chapters)}  figures: {len(records)} "
          f"(captioned: {sum(1 for c in figure_captions.values() if c)})")
    print(f"embedding job: {command_id} (surreal-commands workerが処理)")
    counted = await repo_query(
        "SELECT count() FROM book_figure WHERE source = $s GROUP ALL",
        {"s": ensure_record_id(str(source.id))},
    )
    print(f"book_figure rows: {counted[0]['count'] if counted else 0}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="superbook-pdf markdown output dir")
    ap.add_argument("--pdf", help="original PDF path (stored as the Source asset)")
    ap.add_argument("--title", help="book title (default: md filename)")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--caption-model", default="claude-sonnet-5")
    args = ap.parse_args()
    asyncio.run(
        ingest(
            Path(args.dir),
            Path(args.pdf) if args.pdf else None,
            args.title,
            not args.no_captions,
            args.caption_model,
        )
    )
