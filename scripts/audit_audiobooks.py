"""Sonnet 5 によるオーディオブック品質監視システム。

regexヒューリスティックは「長い実タイトル」を誤検出し、「第N章＋断片」や
「順序崩れ」を見逃す。章構成の健全性は意味理解が要るため、各オーディオブックの
章一覧を Sonnet 5 に渡し、健全/壊れを判定させる。壊れの型（断片タイトル・読み順
崩れ・タイトル欠落/切断・重複・巻末混入）も返させ、修復の優先度付けに使う。

使い方:
    uv run --env-file .env python scripts/audit_audiobooks.py            # 全書籍を監査
    uv run --env-file .env python scripts/audit_audiobooks.py --json     # 壊れ一覧をJSON出力
    uv run --env-file .env python scripts/audit_audiobooks.py --name "本名"  # 1冊だけ
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from open_notebook.database.repository import repo_query  # noqa: E402

MONITOR_MODEL = "claude-sonnet-5"

PROMPT = """あなたはオーディオブックの品質監視員です。ある書籍の「章一覧」を見て、
オーディオブックとして健全な章構成かを判定してください。

健全な章構成の例: 序章/はじめに → 第1章 → 第2章 → … → 終章/おわりに のように、
意味のある章タイトルが読み順(1,2,3…)に並んでいる。日記形式や「エピソードN」等、
本によって形式は違ってよい。

壊れの兆候（あれば issues に該当コードを入れる）:
- "fragment": タイトルが文の途中の断片（例:「決心をしました。らうことこそが」）
- "scrambled": 読み順が崩れている（例: 第1章が最後に来る、章番号が飛ぶ/前後する）
- "missing_title": タイトルが欠落（ただの「第3章」だけ等）や途中で切れている
- "duplicate": 同じ章/部の重複、体裁の揺れ（第1部と第一部が別章）
- "back_matter": 本編でない巻末（出版目録・索引・広告・他書名の羅列）が章になっている
- "too_many": 明らかに章数が多すぎて過分割されている

次のJSONだけを返す（説明文なし）:
{"status": "healthy" または "broken", "issues": ["code", ...], "note": "一言(日本語)"}

--- 書籍名 ---
{title}

--- 章一覧（index: タイトル） ---
{chapters}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _judge(client, title: str, chapters: list[tuple]) -> dict:
    ch_text = "\n".join(f"{i}: {t}" for i, t in chapters)
    prompt = PROMPT.replace("{title}", title).replace("{chapters}", ch_text[:6000])
    resp = client.messages.create(
        model=MONITOR_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        d = _extract_json(raw)
    except Exception:  # noqa: BLE001 - 監視が落ちないよう不明扱い
        return {"status": "unknown", "issues": ["parse_error"], "note": raw[:60]}
    return d


async def main(only: str | None, as_json: bool) -> None:
    import anthropic

    client = anthropic.Anthropic()
    q = "SELECT type::string(id) AS id, name FROM audiobook"
    if only:
        q += " WHERE name = $n"
    abs_ = await repo_query(q, {"n": only} if only else {})

    results = []
    for a in abs_:
        eps = await repo_query(
            "SELECT chapter_index, chapter_title FROM episode "
            "WHERE type::string(audiobook)=$ab",
            {"ab": a["id"]},
        )
        eps = sorted(eps, key=lambda e: e.get("chapter_index", 0) or 0)
        chapters = [(e.get("chapter_index"), (e.get("chapter_title") or "")) for e in eps]
        if not chapters:
            continue
        verdict = _judge(client, a["name"], chapters)
        verdict["name"] = a["name"]
        verdict["chapters"] = len(chapters)
        results.append(verdict)
        mark = "✅" if verdict.get("status") == "healthy" else "⚠"
        issues = ",".join(verdict.get("issues") or [])
        print(f"  {mark} {a['name'][:34]:<36} {verdict.get('status'):8} [{issues}] {verdict.get('note', '')[:30]}")

    broken = [r for r in results if r.get("status") != "healthy"]
    print(f"\n=== 監査結果: 全{len(results)}冊 / 健全{len(results) - len(broken)} / 要対応{len(broken)} ===")
    # 壊れの型別集計
    from collections import Counter

    codes = Counter(c for r in broken for c in (r.get("issues") or []))
    for code, n in codes.most_common():
        print(f"  {code}: {n}冊")
    if as_json:
        out = Path("/tmp/audit_broken.json")
        out.write_text(json.dumps([r["name"] for r in broken], ensure_ascii=False))
        print(f"\n壊れ一覧 → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="1冊だけ監査")
    ap.add_argument("--json", action="store_true", help="壊れ一覧をJSON出力")
    args = ap.parse_args()
    asyncio.run(main(args.name, args.json))
