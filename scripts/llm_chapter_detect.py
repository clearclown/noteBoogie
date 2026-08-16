"""LLMでOCR本の「本物の章」を検出し、source markdown に章見出しを注入する。

DN(superbook-pdf)のスキャン本変換では、markdownの `## ` 見出しがOCRノイズ
（著者名・ページ番号・本文断片）だらけで、真の章構造（第N章/エピソード/漢数字節/
[I][V]…と本ごとにバラバラ）が本文に埋没し、巻末に出版社カタログが混入する。
正規表現ベースの [`inject_chapter_headings`] では捌けないため、意味を理解できる
LLMに「本物の章一覧＋各章本文の開始アンカー＋本編の終端」を返させ、その位置に
`## <章名>` を注入し、巻末（カタログ・索引・広告）を切り落とす。

使い方:
    uv run --env-file .env python scripts/llm_chapter_detect.py --name "サンプル書籍" [--apply]
    # --apply なしは dry-run（検出結果のみ表示、DBは変更しない）
    # --apply で source.full_text を書き換える（元は source.full_text_backup に退避）
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

# 章検出は「構造化抽出＋指示追従＋クリーンなJSON」が肝で、Sonnet 5 が最適
# （指示追従に優れ、安価で大量バッチ向き。Opus の拡張thinkingによる空JSON出力も回避）。
DETECT_MODEL = "claude-sonnet-5"

PROMPT = """あなたはスキャン本のOCRテキストから「本物の章構成」を復元する専門家です。

以下は日本語の書籍をOCRした本文です。markdownの見出し(`#`/`##`)はOCRの誤りで、
著者名・ページ番号・本文の途中・目次などが紛れており、当てになりません。
本文を読んで、**読者が聴く価値のある本編の、本物の章（またはそれに準ずる大きな節）**
を、本文に登場する順に特定してください。

章マーカーの形式は本によって異なります（例: 「第N章」「エピソードN」「一・二…十四」
「[I][V]」「はじめに/序章/終章」など）。形式に依存せず、意味で判断してください。

各章について、次を返してください:
- title: 章の簡潔な表示名（先頭の章番号は残してよい。OCR誤字は自然に補正）
- anchor: その章の**本文が始まる位置**を一意に特定できる、本文からの**逐語コピー**
  （20〜40字程度）。目次の行ではなく、実際に本文が始まる箇所を選ぶこと。逐語で
  なければ位置特定に失敗するため、OCRのまま正確に写すこと。

さらに、本編の後ろに続く**巻末（出版社の目録・広告・全集一覧・索引・他の本の題名の
羅列など、この本の内容ではない部分）**があれば、その巻末が**始まる位置**を
back_matter_anchor として本文からの逐語コピーで返してください。無ければ null。

必ず次のJSONだけを返してください（前後に説明文を付けない）:
{"chapters": [{"title": "...", "anchor": "..."}, ...], "back_matter_anchor": "..." または null}

--- OCR本文（先頭を優先。長い場合は後半を省略しています）---
"""


def _extract_json(text: str) -> dict:
    """モデル出力からJSONを頑健に取り出す。"""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _find_anchor(text: str, anchor: str, search_from: int) -> int:
    """anchorの出現位置(元テキストのオフセット)を search_from 以降で返す。無ければ -1。

    LLMのアンカーには改行や全半角空白のゆらぎが混ざる（例『「サンプル見出し」\\n業績が…』）。
    まず逐語一致を試し、外れたら**空白を畳んだ照合**（元オフセットへ写像）で拾う。
    これをしないと改行入りアンカーが軒並み外れ、章が1つに潰れる（実書籍で発生）。
    """
    if not anchor:
        return -1
    idx = text.find(anchor, search_from)
    if idx != -1:
        return idx
    target = re.sub(r"\s+", "", anchor)
    if len(target) < 6:
        return -1
    # search_from 以降の非空白文字だけを集め、元オフセットへの写像を持つ
    compact_chars: list[str] = []
    offsets: list[int] = []
    for i in range(search_from, len(text)):
        c = text[i]
        if not c.isspace():
            compact_chars.append(c)
            offsets.append(i)
    compact = "".join(compact_chars)
    pos = compact.find(target)
    if pos == -1:
        pos = compact.find(target[:16])  # 先頭16字での前方一致フォールバック
    return offsets[pos] if pos != -1 else -1


# 章タイトル先頭の章マーカー（第N章 / 一〜十 / エピソードN / 数字 / プロローグ等）
_MARKER_RE = re.compile(
    r"^(第\s*[0-9０-９一二三四五六七八九十百]+\s*[章節部]"
    r"|[一二三四五六七八九十]+|[0-9０-９]+"
    r"|エピソード\s*[0-9０-９]+|プロローグ|エピローグ|序章|終章|はじめに|おわりに|付録)\s*"
)


def _title_core(title: str) -> str:
    """タイトルから章マーカーを剥がした中身（実在検証の対象）。"""
    return _MARKER_RE.sub("", (title or "").strip()).strip("　 :：・-—「」()（）")


def title_is_grounded(title: str, text: str, near: int = -1) -> bool:
    """タイトルの中身が本文に実在するか（LLMの生成的でっち上げを弾く）。

    章マーカーだけ（「第3章」等、中身なし）や、中身の4字以上の連続片が本文の
    どこか（可能なら anchor 近傍）に現れれば grounded とみなす。純粋な創作を排除。
    """
    core = _title_core(title)
    if not core:
        return True  # マーカーのみ（第3章 等）は中身が無いので検証対象外＝許容
    compact_core = re.sub(r"\s+", "", core)
    if len(compact_core) < 4:
        return True  # 3字以下は意味的検証が不能。短い実タイトルを弾かないため許容
    scope = text
    if near >= 0:
        scope = text[max(0, near - 2000): near + 4000]
    compact_scope = re.sub(r"\s+", "", scope)
    # 4字以上の連続部分列が本文に存在するか
    for size in (len(compact_core), 8, 6, 4):
        if size < 4 or size > len(compact_core):
            continue
        for i in range(0, len(compact_core) - size + 1):
            if compact_core[i : i + size] in compact_scope:
                return True
    return False


def coverage_warnings(positions: list[int], total_len: int) -> list[str]:
    """章境界の決定論的整合チェック（欠落・偏り・過大章を検出）。"""
    warns: list[str] = []
    if not positions:
        return ["章が0（未分割）"]
    sp = sorted(positions)
    # 各章の本文サイズ
    bounds = sp + [total_len]
    spans = [bounds[i + 1] - bounds[i] for i in range(len(sp))]
    # 先頭章より前（前付け）が大きすぎる＝早い章を取りこぼしている疑い
    if sp[0] > total_len * 0.30:
        warns.append(f"前付けが本文の{sp[0] * 100 // total_len}%＝早い章の取りこぼし疑い")
    # 単一章が極端に大きい＝分割漏れ
    biggest = max(spans)
    if len(sp) > 1 and biggest > total_len * 0.55:
        warns.append(f"最大章が本文の{biggest * 100 // total_len}%＝分割漏れ疑い")
    # ほぼ空の章
    tiny = sum(1 for s in spans if s < 300)
    if tiny:
        warns.append(f"ほぼ空の章が{tiny}個")
    return warns


# 前付け・後付けの見出し語（読み順の先頭/末尾に固定するグループ判定に使う）
_FRONT_RE = re.compile(
    r"^(はじめに|まえがき|序章|序文|序$|序\s|プロローグ|刊行者の言葉|訳者例言|凡例|目次)"
)
_BACK_RE = re.compile(
    r"^(おわりに|終章|結語|エピローグ|あとがき|付録|補遺|索引|参考文献|人名の説明)"
)
# 章番号の抽出（第N章 / N章 / エピソードN / 先頭の数字）。漢数字も算用数字も拾う。
_KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
              "八": 8, "九": 9, "十": 10}


def _parse_chapter_number(title: str) -> "int | None":
    """章タイトルから章番号を取り出す。取れなければ None。

    「第12章」「第十二章」「エピソード3」「3 戦略」など、書籍ごとに違う表記を
    同じ整数へ落とす。番号が取れた章が十分あるときだけ読み順の並べ替えを行う。
    """
    t = (title or "").strip()
    m = re.match(r"^第\s*([0-9０-９]+|[一二三四五六七八九十]+)\s*[章部節]", t)
    if not m:
        m = re.match(r"^(?:エピソード|Chapter|Part)\s*([0-9０-９]+)", t, re.IGNORECASE)
    if not m:
        m = re.match(r"^([0-9０-９]+)[\s.．、]", t)
    if not m:
        # 「十四 資本と労働との闘争」のように、第/章を伴わない漢数字だけの節番号
        # （古い訳書に多い）。後続に区切りがある場合のみ番号とみなす。
        m = re.match(r"^([一二三四五六七八九十]+)[\s　]", t)
    if not m:
        return None
    raw = m.group(1)
    raw = raw.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if raw.isdigit():
        return int(raw)
    # 漢数字（十/十N/N十N の簡易解釈。書籍の章番号は概ね 1〜99）
    total, tens = 0, 0
    for ch in raw:
        v = _KANJI_NUM.get(ch)
        if v is None:
            return None
        if v == 10:
            tens = (tens or 1) * 10
            total += tens
            tens = 0
        else:
            total += v
    return total or None


def chapter_order_key(title: str, index: int) -> tuple:
    """論理的な読み順のソートキー (group, number, index)。

    group: 0=前付け（はじめに等） / 1=本編 / 2=後付け（おわりに等）。
    本編は章番号順。番号が無い章は元の並び（index）を保つ。
    """
    t = (title or "").strip()
    if _FRONT_RE.match(t):
        return (0, index, index)
    if _BACK_RE.match(t):
        return (2, index, index)
    num = _parse_chapter_number(t)
    return (1, num if num is not None else 10**6 + index, index)


def should_reorder(titles: list[str], min_numbered_ratio: float = 0.6) -> tuple:
    """読み順の並べ替えを行ってよいか判定する（安全側に倒す純関数）。

    章番号が十分に取れないと並べ替えは「改善」でなく「破壊」になり得るため、
    本編章の 60% 以上から番号が取れるときだけ許可する。返り値は
    (可否, 理由)。
    """
    body = [t for t in titles if not _FRONT_RE.match((t or "").strip())
            and not _BACK_RE.match((t or "").strip())]
    if len(body) < 2:
        return (False, "本編章が2つ未満（並べ替え不要）")
    numbered = [t for t in body if _parse_chapter_number(t) is not None]
    ratio = len(numbered) / len(body)
    if ratio < min_numbered_ratio:
        return (False, f"章番号が取れたのは {len(numbered)}/{len(body)} のみ（並べ替えは危険）")
    nums = [_parse_chapter_number(t) for t in numbered]
    if len(set(nums)) != len(nums):
        return (False, "章番号が重複している（並べ替えは危険）")
    return (True, f"章番号を {len(numbered)}/{len(body)} から取得")


def build_injected_source(
    ft: str, chapters: list[dict], back_matter_anchor: "str | None",
    reorder: bool = True,
) -> dict:
    """章検出結果(chapters)を DN本文(ft)へ適用し、注入済みテキストを組む（純関数）。

    決定論的な中核処理（LLM/DB非依存）。ハルシネーション対策の要:
    - 巻末アンカーで本編だけに切る
    - 既存のOCRノイズ見出し(`#`〜`######`)を平文へ降格
    - 章アンカーは**逐語一致必須**（一致しなければ棄却＝存在しない位置に注入しない）
    - タイトルは**本文に実在必須**（未実在＝生成的でっち上げはマーカーのみに縮退）
    返り値: {"text", "chapter_count", "ungrounded", "warnings", "end"}。
    """
    # 巻末の切り落とし位置
    end = len(ft)
    if back_matter_anchor:
        # 最初の章位置以降で巻末アンカーを探す（前付けの誤検出回避のため素朴に0から）
        bpos = _find_anchor(ft, back_matter_anchor, 0)
        if bpos != -1:
            end = bpos

    trimmed = ft[:end]
    stripped = re.sub(r"(?m)^#{1,6}[ \t]+", "", trimmed)

    # 1. 各章のアンカーを**独立に**探す（文書内のどこにあってもよい）。
    #    従来は前章より後ろだけを探していたため、物理順が壊れた本（第2章の本文が
    #    第1章より前にある等）で後続章が軒並み棄却されていた。
    found: list[tuple[int, str]] = []
    ungrounded = 0
    used: set[int] = set()
    for ch in chapters:
        anchor = ch.get("anchor", "")
        title = (ch.get("title") or "").strip()
        pos = _find_anchor(stripped, anchor, 0)
        if pos == -1 or not title or pos in used:
            continue
        if not title_is_grounded(title, stripped, near=pos):
            ungrounded += 1
            marker = _MARKER_RE.match(title)
            title = marker.group(0).strip() if marker else title
        used.add(pos)
        found.append((pos, title))

    warnings = coverage_warnings([p for p, _ in found], len(stripped))
    if not found:
        return {"text": stripped, "chapter_count": 0, "ungrounded": ungrounded,
                "warnings": warnings, "end": end, "reordered": False,
                "reorder_reason": "章が特定できず"}

    # 2. 物理順に並べてセグメント化（各章 = 自分のアンカーから次のアンカー直前まで）
    by_pos = sorted(found)
    preamble = stripped[: by_pos[0][0]]
    segments: list[dict] = []
    for i, (pos, title) in enumerate(by_pos):
        seg_end = by_pos[i + 1][0] if i + 1 < len(by_pos) else len(stripped)
        segments.append({"title": title, "body": stripped[pos:seg_end]})

    # 3. 論理順（章番号順）に並べ替える。番号が十分取れない場合は物理順のまま。
    reordered = False
    ok, reason = should_reorder([s["title"] for s in segments]) if reorder else (
        False, "並べ替え無効"
    )
    if ok:
        ordered = sorted(
            enumerate(segments),
            key=lambda t: chapter_order_key(t[1]["title"], t[0]),
        )
        new_segments = [s for _, s in ordered]
        reordered = [s["title"] for s in new_segments] != [s["title"] for s in segments]
        segments = new_segments

    # 4. 見出しを付けて連結。本文の欠落が起きないことを決定論で検証する。
    out = preamble + "".join(f"## {s['title']}\n\n{s['body']}" for s in segments)
    body_chars = len(preamble) + sum(len(s["body"]) for s in segments)
    if body_chars != len(stripped):
        warnings.append(f"本文長が不一致（{body_chars} != {len(stripped)}）")

    return {
        "text": out,
        "chapter_count": len(segments),
        "ungrounded": ungrounded,
        "warnings": warnings,
        "end": end,
        "reordered": reordered,
        "reorder_reason": reason,
    }


async def detect(name: str, apply: bool) -> None:
    ab = await repo_query(
        "SELECT type::string(source_id) AS sid FROM audiobook WHERE name=$n", {"n": name}
    )
    if not ab:
        sys.exit(f"audiobook『{name}』が見つかりません")
    sid = ab[0]["sid"]
    r = await repo_query("SELECT full_text FROM source WHERE type::string(id)=$s", {"s": sid})
    ft: str = r[0]["full_text"] if r else ""
    if not ft:
        sys.exit("full_text が空です")

    import anthropic

    client = anthropic.Anthropic()
    # 巨大本はプロンプトを圧迫するため先頭12万字に制限（章検出には十分）
    body = ft[:120_000]
    resp = client.messages.create(
        model=DETECT_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": PROMPT + body}],
    )
    raw = next((b.text for b in resp.content if b.type == "text"), "")
    if not raw.strip():
        sys.exit(f"LLM応答が空（stop_reason={resp.stop_reason}）。再実行してください。")
    try:
        data = _extract_json(raw)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"JSON解析失敗: {e}\n先頭200字: {raw[:200]!r}")
    chapters = data.get("chapters") or []
    back = data.get("back_matter_anchor")
    print(f"LLM検出章数: {len(chapters)}  巻末アンカー: {back[:30] if back else 'なし'!r}")

    # アンカー位置を文書順で確定（前章より後ろを探す）
    injected: list[tuple[int, str]] = []
    cursor = 0
    for ch in chapters:
        pos = _find_anchor(ft, ch.get("anchor", ""), cursor)
        title = (ch.get("title") or "").strip()
        if pos == -1:
            print(f"  ⚠ 位置特定失敗: {title[:30]}  anchor={ch.get('anchor', '')[:24]!r}")
            continue
        injected.append((pos, title))
        cursor = pos + 1
        print(f"  ✅ pos={pos:>6}  ## {title[:40]}")

    # 巻末の切り落とし位置
    end = len(ft)
    if back:
        bpos = _find_anchor(ft, back, injected[0][0] if injected else 0)
        if bpos != -1:
            end = bpos
            print(f"  ✂ 巻末を pos={bpos} 以降で除去（{len(ft) - bpos}字）")

    if not injected:
        print("章を特定できませんでした（手動対応が必要）")
        return
    print(f"\n結果: {len(injected)}章 / 本文 {end}字（元 {len(ft)}字）")

    if not apply:
        print("[dry-run] --apply でDBに反映します")
        return

    result = build_injected_source(ft, chapters, back)
    stripped = result["text"]
    if result["warnings"]:
        print("  ⚠ カバレッジ警告: " + " / ".join(result["warnings"]))
    if result["ungrounded"]:
        print(f"  ⚠ 未実在タイトル {result['ungrounded']}件（マーカーのみに縮退）")
    if result.get("reordered"):
        print(f"  ↕ 読み順を章番号順に再構成しました（{result.get('reorder_reason')}）")
    elif result.get("reorder_reason"):
        print(f"  ・読み順の並べ替えは見送り: {result.get('reorder_reason')}")

    await repo_query(
        "UPDATE source SET full_text_backup = full_text, full_text = $t WHERE type::string(id)=$s",
        {"t": stripped, "s": sid},
    )
    print(
        f"✅ source を更新（元は full_text_backup に退避）: "
        f"{len(stripped)}字 / {result['chapter_count']}章（既存ノイズ見出しは除去済み）"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="audiobook名（=本のタイトル）")
    ap.add_argument("--apply", action="store_true", help="DBのsource.full_textを書き換える")
    args = ap.parse_args()
    asyncio.run(detect(args.name, args.apply))
