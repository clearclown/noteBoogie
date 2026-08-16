"""OCR本のMarkdownに、本文中の「第N章/第N部」ランニングヘッダから章見出しを自動注入する。

SuperBook(DN)変換器はスキャン本で章見出しをMarkdownの `#`/`##` として検出できず、
章タイトルが本文行として落ちることが多い（manifestのchaptersも0になる）。すると
gatewayの章分割が「全体1章」または誤爆になり、オーディオブックの章立てが壊れる。

多くの和書は各ページ上部に「第N章 タイトル」の柱（running header）を持ち、OCRは
これを本文行として何度も出力する。この反復こそ章の在処の信頼できる信号:

  - 柱として反復する章行 → 反復回数が多い（>= MIN_REPEAT）→ 本物の章
  - 目次の一覧・本文中の相互参照（「第3章で述べたように」）→ 反復が少ない → 除外

各「本物の章」の初出行に `## 第N章 タイトル` を挿入する。gateway(chapters.rs)は
ATX見出しで章分割し、title_keyの first-substantial-occurrence で残りの柱を吸収する。

使い方:
    uv run python scripts/inject_chapter_headings.py data/books/<dir>/<book>.md [--dry-run]

冪等: 既に `## 第N章` が入っている場合は該当章をスキップする。元ファイルは .orig に退避。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

# 「第<数字>章」「第<数字>部」で始まる行。数字は算用/全角/漢数字を許容。
CHAPTER_RE = re.compile(r"^第\s*([0-9０-９一二三四五六七八九十百]+)\s*([章部])\s*(.*)$")

# 章としてカウントする最小反復回数（柱は各ページに出るので通常2桁になる）。
MIN_REPEAT = 3
# 見出し行を除いた実体文字数がこれ未満の章開始候補は「薄い」ので採用しない。
MIN_BODY_CHARS = 200

# 章タイトルの最大文字数。これを超えたら本文が混入しているとみなし整形する。
MAX_TITLE_CHARS = 30


def _clean_title(title: str) -> str:
    """OCRの柱行からタイトルだけを取り出す（本文の混入・注記を落とす）。

    翻訳書などで柱行が「第N章 (本書では第M章)では戦略を…」のように本文へ流れる
    ことがある。括弧注記を除き、最初の文区切りで切り、長すぎれば丸める。
    """
    import re as _re

    # 「(本書では…)」等の丸括弧・全角括弧の注記を除去
    title = _re.sub(r"[(（][^)）]*[)）]", "", title).strip()
    # 最初の句読点・助詞的接続の手前で切る（タイトルは体言止めが多い）
    for sep in ("。", "、", "では", "として", "について", "という"):
        idx = title.find(sep)
        if 0 < idx <= MAX_TITLE_CHARS:
            title = title[:idx]
            break
    title = title.strip("　 ·・-—「」")
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS]
    return title.strip()


def _body_chars_after(lines: list[str], idx: int, window: int = 40) -> int:
    """idx行の直後 window 行の実体文字数（空白除く）。章開始らしさの判定に使う。"""
    text = "".join(lines[idx + 1 : idx + 1 + window])
    return len("".join(text.split()))


def detect_chapter_starts(lines: list[str]) -> list[tuple[int, str, str]]:
    """(挿入先の0基底行, 章キー, 見出しテキスト) を文書順で返す。

    章キーは「第N章」正規形（番号+単位）。各章キーについて、実体本文が続く
    「初出」を採用する（目次の薄い初出は飛ばす）。反復が MIN_REPEAT 未満の
    章キーは相互参照とみなし除外する。
    """
    occurrences: dict[str, list[int]] = defaultdict(list)
    titles: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = CHAPTER_RE.match(line.strip())
        if not m:
            continue
        num, unit, title = m.group(1), m.group(2), (m.group(3) or "").strip()
        key = f"第{num}{unit}"
        occurrences[key].append(i)
        # 最も長いタイトルを代表として採用（OCRの途切れ対策）。
        if title and len(title) > len(titles.get(key, "")):
            titles[key] = title

    starts: list[tuple[int, str, str]] = []
    for key, idxs in occurrences.items():
        if len(idxs) < MIN_REPEAT:
            continue  # 相互参照・単発 → 章ではない
        # 実体本文が続く初出を章開始とする。
        start = next(
            (i for i in idxs if _body_chars_after(lines, i) >= MIN_BODY_CHARS),
            idxs[0],
        )
        title = _clean_title(titles.get(key, ""))
        heading = f"## {key} {title}".rstrip()
        starts.append((start, key, heading))

    starts.sort(key=lambda t: t[0])
    return starts


def inject(md_path: Path, dry_run: bool = False) -> int:
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    if any(re.match(r"^##\s+第", ln) for ln in lines):
        print("既に章見出しが存在します。スキップ（冪等）。")
        return 0

    starts = detect_chapter_starts(lines)
    if not starts:
        print("反復する『第N章』ランニングヘッダを検出できませんでした（手動注入が必要）。")
        return 0

    print(f"検出した章: {len(starts)}")
    for _, key, heading in starts:
        print(f"  {heading[:50]}")

    if dry_run:
        print("[dry-run] 変更は書き込みません。")
        return len(starts)

    for start, _key, heading in sorted(starts, key=lambda t: t[0], reverse=True):
        lines.insert(start, "")
        lines.insert(start, heading)

    shutil.copy(md_path, str(md_path) + ".orig")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"注入完了: {len(starts)}章（元ファイルは {md_path.name}.orig に退避）")
    return len(starts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("md", help="対象の .md（SuperBook出力）")
    ap.add_argument("--dry-run", action="store_true", help="検出のみ・書き込みなし")
    args = ap.parse_args()
    md = Path(args.md)
    if not md.exists():
        sys.exit(f"not found: {md}")
    inject(md, dry_run=args.dry_run)
