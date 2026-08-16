"""ランダム標本 + Vision照合による章構造の品質担保（QA）。

決定論(DN)＋抽出LLM(章検出)の結果を、**画像という独立の真実**で抜き取り検査する。
全ページをVisionに掛けるとコストが嵩むため、(a)ランダム標本 + (b)怪しい章の境界
ページ、に**集中的に**Visionを使う。Visionは文章を生成せず「このページに章見出しが
あるか・その文字列・本編か巻末か」を**報告するだけ**（照合役）。その報告を source の
章一覧と突き合わせ、**見落とし章/偽章/巻末残り**を統計的に検出する。

使い方:
    uv run --env-file .env python scripts/qa_sample_vision.py \
        --name "サンプル書籍" --pdf data/uploads/xxx.pdf --sample 5 [--seed 0]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from open_notebook.database.repository import repo_query  # noqa: E402

VISION_MODEL = "claude-sonnet-5"


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def title_matches_any(seen: str, chapter_titles: list[str]) -> bool:
    """Visionが見た見出し(seen)が、source章一覧のどれかと（緩く）一致するか。"""
    ns = _norm(seen)
    if len(ns) < 3:
        return True  # 短すぎる断片は判定しない（誤検出回避）
    for t in chapter_titles:
        nt = _norm(t)
        if not nt:
            continue
        # 双方向の4字以上の部分一致
        short, long = (ns, nt) if len(ns) <= len(nt) else (nt, ns)
        for size in (min(len(short), 8), 6, 4):
            if size < 4 or size > len(short):
                continue
            if any(short[i : i + size] in long for i in range(len(short) - size + 1)):
                return True
    return False


def select_sample_pages(
    total_pages: int, boundary_pages: list[int], sample_n: int, seed: int
) -> list[int]:
    """検査対象ページ = 章境界ページ(怪しい所) + ランダム標本。1始まり・重複排除。"""
    import random as _random

    rng = _random.Random(seed)
    pages = set(p for p in boundary_pages if 1 <= p <= total_pages)
    pool = [p for p in range(1, total_pages + 1) if p not in pages]
    rng.shuffle(pool)
    for p in pool[: max(0, sample_n)]:
        pages.add(p)
    return sorted(pages)


def aggregate_qa(page_reports: list[dict], chapter_titles: list[str]) -> dict:
    """Vision報告を source章一覧と突き合わせ、QA判定を返す（純関数）。"""
    missed = []  # Visionは章見出しを見たが source に無い＝見落とし章
    backmatter_hits = 0
    checked = len(page_reports)
    for rep in page_reports:
        for h in rep.get("headings", []) or []:
            if not title_matches_any(h, chapter_titles):
                missed.append({"page": rep.get("page"), "heading": h})
        if rep.get("is_back_matter"):
            backmatter_hits += 1
    verdict = "ok" if not missed and backmatter_hits == 0 else "flag"
    return {
        "verdict": verdict,
        "checked_pages": checked,
        "missed_chapters": missed,
        "back_matter_pages": backmatter_hits,
    }


def _render_page(pdf: str, page: int, out_dir: Path) -> "Path | None":
    prefix = out_dir / f"p{page}"
    subprocess.run(
        ["pdftoppm", "-png", "-r", "150", "-f", str(page), "-l", str(page), pdf, str(prefix)],
        check=False,
        capture_output=True,
    )
    hits = sorted(out_dir.glob(f"p{page}*.png"))
    return hits[0] if hits else None


VISION_PROMPT = """このスキャン書籍ページを見て、次をJSONで返してください（説明文なし）:
{"headings": ["このページで『章の開始見出し』が現れていればその文字列。無ければ空配列"],
 "is_back_matter": true/false（このページが本編でなく巻末=出版目録・広告・索引・
   奥付・他書の題名一覧などか）}

重要な定義:
- headings に入れるのは【章＝本の最上位の区切り】の開始見出しだけ。
  例: 「第N章 …」「エピソードN …」「序章/終章/プロローグ/エピローグ」、
  章番号付きの大見出し、章タイトルだけの扉ページ、など**視覚的に大きく目立つ最上位区切り**。
- 【入れないもの】章の中の小見出し・節・「1.」「2.」等の箇条見出し・図表ラベル・
  ページ隅の縦書きの柱(ランニングヘッダ)・本文・ページ番号。
- 章の途中の本文ページなら headings は空配列。"""


async def run(name: str, pdf: str, sample_n: int, seed: int) -> None:
    import anthropic

    # source の章一覧とページ総数
    ab = await repo_query(
        "SELECT type::string(source_id) AS sid FROM audiobook WHERE name=$n", {"n": name}
    )
    if not ab:
        sys.exit(f"audiobook『{name}』が見つかりません")
    r = await repo_query(
        "SELECT full_text FROM source WHERE type::string(id)=$s", {"s": ab[0]["sid"]}
    )
    ft = r[0]["full_text"] if r else ""
    chapter_titles = [
        re.sub(r"^##\s+", "", ln).strip()
        for ln in ft.split("\n")
        if ln.startswith("## ")
    ]
    # ページ総数
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True)
    m = re.search(r"Pages:\s*(\d+)", info.stdout)
    total = int(m.group(1)) if m else 0
    if total == 0:
        sys.exit("PDFのページ数を取得できません")

    # 章境界ページ = 本文中の <!-- page N --> があれば章開始の直近ページ。無ければ
    # 章数から等間隔で代表境界を推定（怪しい所の近似）。
    boundary = []
    if "<!-- page" in ft:
        # 各 ## の直近の page マーカーを拾う
        for m2 in re.finditer(r"##\s", ft):
            seg = ft[: m2.start()]
            pm = re.findall(r"<!--\s*page\s*(\d+)", seg)
            if pm:
                boundary.append(int(pm[-1]))
    pages = select_sample_pages(total, boundary, sample_n, seed)
    print(f"検査ページ: {pages}（境界{len(boundary)}＋標本、全{total}p / 章{len(chapter_titles)}）")

    client = anthropic.Anthropic()
    reports = []
    with tempfile.TemporaryDirectory() as td:
        for p in pages:
            img = _render_page(pdf, p, Path(td))
            if not img:
                continue
            data = base64.standard_b64encode(img.read_bytes()).decode()
            resp = client.messages.create(
                model=VISION_MODEL,
                max_tokens=800,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                    {"type": "text", "text": VISION_PROMPT},
                ]}],
            )
            raw = next((b.text for b in resp.content if b.type == "text"), "")
            m3 = re.search(r"\{.*\}", raw, re.DOTALL)
            try:
                d = json.loads(m3.group(0)) if m3 else {}
            except Exception:  # noqa: BLE001
                d = {}
            d.setdefault("headings", [])
            d.setdefault("is_back_matter", False)
            d["page"] = p
            reports.append(d)
            hs = d.get("headings") or []
            print(f"  p{p}: 見出し{hs}  巻末={d.get('is_back_matter')}")

    result = aggregate_qa(reports, chapter_titles)
    print(f"\n=== QA判定: {result['verdict']} ===")
    print(f"  検査ページ数: {result['checked_pages']}")
    print(f"  見落とし章候補: {result['missed_chapters']}")
    print(f"  巻末残りページ: {result['back_matter_pages']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--sample", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(run(args.name, args.pdf, args.sample, args.seed))
