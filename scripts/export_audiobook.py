"""オーディオブックを「1フォルダ・人間が読めるファイル名」でエクスポートする。

内部の保存形式（data/podcasts/episodes/<uuid>/audio/<uuid>.mp3）は API 配信の
契約（#1030 の相対パス解決）なので変えず、聴取・持ち出し用に整理したコピーを
作る:

    data/audiobooks/<本のタイトル>/
        00_コンサル頭のつくり方.mp3
        01_はじめに.mp3
        ...
        playlist.m3u8

Usage:
    uv run --env-file .env python scripts/export_audiobook.py \
        --audiobook audiobook:xxxx [--out data/audiobooks] [--link]

--link はコピーの代わりにハードリンク（ディスク節約。同一ボリューム限定）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def safe_filename(title: str, max_len: int = 60) -> str:
    """ファイル名に使えない文字と柱由来の記号ノイズを除去する。"""
    cleaned = re.sub(r'[/\\:*?"<>|]', "", title)
    cleaned = re.sub(r"[|｜]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ・·.")
    return (cleaned or "無題")[:max_len]


async def export(audiobook_id: str, out_root: Path, link: bool) -> Path:
    from open_notebook.config import PODCASTS_FOLDER
    from open_notebook.database.repository import repo_query

    rows = await repo_query(
        "SELECT name FROM audiobook WHERE type::string(id) = $ab", {"ab": audiobook_id}
    )
    if not rows:
        sys.exit(f"{audiobook_id} が見つかりません")
    book_title = safe_filename(rows[0].get("name") or "audiobook")

    chapters = await repo_query(
        "SELECT chapter_index, chapter_title, audio_file FROM episode "
        "WHERE type::string(audiobook) = $ab ORDER BY chapter_index",
        {"ab": audiobook_id},
    )
    out_dir = out_root / book_title
    out_dir.mkdir(parents=True, exist_ok=True)

    exported: list[str] = []
    skipped = 0
    for ch in chapters:
        audio_rel = ch.get("audio_file")
        if not audio_rel:
            skipped += 1
            continue
        src = Path(PODCASTS_FOLDER) / audio_rel
        if not src.exists():
            print(f"  ! 音源が見つかりません: {src}")
            skipped += 1
            continue
        index = ch.get("chapter_index") or 0
        name = f"{index:02d}_{safe_filename(ch.get('chapter_title') or f'第{index}章')}.mp3"
        dst = out_dir / name
        if dst.exists():
            dst.unlink()
        if link:
            os.link(src, dst)
        else:
            shutil.copy2(src, dst)
        exported.append(name)
        print(f"  {name}")

    # 再生順プレイリスト（VLC 等でそのまま連続再生できる）
    playlist = out_dir / "playlist.m3u8"
    playlist.write_text("#EXTM3U\n" + "\n".join(exported) + "\n", encoding="utf-8")

    print(f"\n{len(exported)}章を {out_dir}/ へ出力（スキップ {skipped}）")
    print(f"プレイリスト: {playlist}")
    return out_dir


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--audiobook", required=True)
    ap.add_argument("--out", default="data/audiobooks")
    ap.add_argument("--link", action="store_true")
    args = ap.parse_args()
    asyncio.run(export(args.audiobook, Path(args.out), args.link))
