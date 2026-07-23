"""章ごとのインサイト（要約・キーポイント）を生成して Source に付与する。

Open Notebook ネイティブの insights 機構（Sources UI のインサイトタブに表示、
自動で埋め込みも付く）を書籍に活用する。章分割ロジックを重複させないため、
生成済みオーディオブックの章エピソード（episode.content）を章の単位として
そのまま使う。

Usage:
    uv run --env-file .env python scripts/generate_chapter_insights.py \
        --audiobook audiobook:xxxx [--model anthropic:claude-haiku-4-5] \
        [--min-chars 1000] [--dry-run]

前提: surreal-commands worker が起動していること（insight 作成と埋め込みは
fire-and-forget コマンドとして処理される）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_transcript import generate_with_model  # noqa: E402

INSIGHT_PROMPT = (
    "以下はビジネス書の1章の本文です。この章のインサイトを日本語で作成してください。\n"
    "構成: ①この章の要約（3〜4文） ②キーポイント（箇条書き3〜5点、本文の用語を使う）"
    " ③実務への示唆（1〜2文）。\n"
    "本文に無い固有名詞・数値・事例を創作しないこと。前置きなしで本文のみ出力。\n\n"
    "--- 章本文 ---\n"
)


def build_insight_prompt(content: str) -> str:
    return INSIGHT_PROMPT + content


async def load_substantial_chapters(audiobook_id: str, min_chars: int) -> tuple[str, list[dict]]:
    """(source_id, [{chapter_index, chapter_title, content}]) を返す。

    目次由来の薄い章にインサイトを作っても雑音になるため min_chars で足切り。
    """
    from open_notebook.database.repository import repo_query

    ab = await repo_query(
        "SELECT source_id FROM audiobook WHERE type::string(id) = $ab",
        {"ab": audiobook_id},
    )
    if not ab or not ab[0].get("source_id"):
        sys.exit(f"{audiobook_id} が見つからないか source_id がありません")
    rows = await repo_query(
        "SELECT chapter_index, chapter_title, content, string::len(content) AS content_len "
        "FROM episode WHERE type::string(audiobook) = $ab ORDER BY chapter_index",
        {"ab": audiobook_id},
    )
    chapters = [r for r in rows if (r.get("content_len") or 0) >= min_chars]
    return ab[0]["source_id"], chapters


async def run(args: argparse.Namespace) -> None:
    provider, _, model = args.model.partition(":")
    source_id, chapters = await load_substantial_chapters(args.audiobook, args.min_chars)
    print(f"source: {source_id} / 対象章: {len(chapters)}（{args.min_chars}字未満は除外）")
    if args.dry_run:
        for c in chapters:
            print(f"  ch{c['chapter_index']}: {c['chapter_title']} ({c['content_len']:,}字)")
        return

    # commands はリポジトリ直下パッケージ（未インストール）。insight/embed の
    # コマンド登録に import が必要（ingest_book.py と同じ理由）。
    import commands.embedding_commands  # noqa: F401
    from open_notebook.domain.notebook import Source

    source = await Source.get(source_id)
    total_in = total_out = 0
    created = 0
    for c in chapters:
        title = c.get("chapter_title") or f"第{c['chapter_index']}章"
        try:
            text, in_tok, out_tok = await generate_with_model(
                provider, model, "", build_insight_prompt(c["content"])
            )
        except Exception as e:  # noqa: BLE001 - per-chapter best effort
            print(f"  ch{c['chapter_index']} 生成失敗: {e}")
            continue
        total_in += in_tok
        total_out += out_tok
        await source.add_insight(f"章インサイト: {title}", text)
        created += 1
        print(f"  ch{c['chapter_index']} {title}: insight投入 ({out_tok}tok)")

    print(f"\n{created}件のinsightコマンドを投入（workerが作成+埋め込み）")
    print(f"tokens: in={total_in} out={total_out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audiobook", required=True)
    ap.add_argument("--model", default="anthropic:claude-haiku-4-5",
                    help="要約なので既定は低コストモデル")
    ap.add_argument("--min-chars", type=int, default=1000,
                    help="この文字数未満の章はスキップ")
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
