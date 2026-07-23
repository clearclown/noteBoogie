"""Book Navigator 台本の品質評価ハーネス。

台本（モノローグ）を章コンテンツに対して自動採点する。コスト/モデル切替の
判断材料であり、プロンプト最適化（scripts/optimize_briefing.py）の報酬関数
でもある。

指標（すべて決定的・LLM不要）:
  - structure:    briefing が要求する3部構成の遵守率
                  (課題提示 → 「1つ目は/2つ目は/3つ目は」 → アクションプラン)
  - grounding:    台本中の固有名詞・数値が章コンテンツに実在する割合
                  (1 - grounding = 捏造リスク)
  - politeness:   敬体(です/ます)の文末一貫性
  - length_ratio: 台本文字数 / コンテンツ文字数（薄い章の水増し検出）

オプションで LLM-as-judge（--judge、忠実性1-5）を追加できる。

Usage:
    # DB上のオーディオブックを採点
    uv run --env-file .env python scripts/eval_transcript.py --audiobook audiobook:xxx

    # モデル比較: 同じ章を各モデルで台本化して採点・コスト併記
    uv run --env-file .env python scripts/eval_transcript.py \
        --compare anthropic:claude-sonnet-5 anthropic:claude-haiku-4-5 \
                  deepseek:deepseek-v4-flash \
        --audiobook audiobook:xxx --chapters 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Metric primitives (pure, unit-tested)
# ---------------------------------------------------------------------------

STRUCTURE_PATTERNS: list[tuple[str, str]] = [
    ("problem", r"この章[はでが].{0,30}(悩み|問題|課題|疑問|答え)"),
    ("point1", r"1\s*つ\s*目|一つ目|まず1点目"),
    ("point2", r"2\s*つ\s*目|二つ目"),
    ("point3", r"3\s*つ\s*目|三つ目"),
    ("action", r"アクションプラン|明日から|実践|やってみ|取り組んで"),
]

POLITE_ENDINGS = re.compile(r"(です|ます|ました|ません|でしょう|ましょう|ですね|ますね)[。！？]?$")


def transcript_text(transcript: object) -> str:
    """Flatten a transcript (DB object / list of dialogues / str) to text."""
    if transcript is None:
        return ""
    if isinstance(transcript, str):
        return transcript
    if isinstance(transcript, dict):
        inner = transcript.get("transcript", transcript)
        return transcript_text(inner)
    if isinstance(transcript, list):
        return "\n".join(
            d.get("dialogue", "") if isinstance(d, dict) else str(d) for d in transcript
        )
    return str(transcript)


def structure_score(text: str) -> dict:
    """構成遵守: 必須要素の出現率と「1つ目→2つ目→3つ目」の順序。"""
    found: dict[str, int] = {}
    for name, pattern in STRUCTURE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            found[name] = m.start()
    ordered = all(
        found.get(a, -1) < found.get(b, 10**12)
        for a, b in [("point1", "point2"), ("point2", "point3")]
        if a in found and b in found
    )
    score = len(found) / len(STRUCTURE_PATTERNS)
    if {"point1", "point2", "point3"} <= found.keys() and not ordered:
        score -= 0.2
    return {"score": round(max(score, 0.0), 3), "found": sorted(found), "ordered": ordered}


# 「事実」候補: カタカナ語(3+)、漢字連(2+)、単位付き数値
_FACT_RE = re.compile(r"[ア-ヴー]{3,}|[一-龠]{2,}|\d[\d,.]*\s*(?:%|％|円|人|年|倍|億|万)")

# 台本側の話法・briefing由来で本文に無くて当然の語（誤検知の抑制）
_SPEECH_STOPWORDS = {
    "皆さん", "今日", "今回", "本章", "章", "最初", "最後", "重要", "具体的",
    "説明", "紹介", "理解", "意識", "自分", "仕事", "毎日", "簡単", "大切",
    "結論", "行動", "実践", "方法", "内容", "部分", "全体", "場合", "とき",
    "ポイント", "ステップ", "アクション", "アクションプラン", "イメージ", "メンター",
}


def extract_fact_terms(text: str) -> set[str]:
    return {t for t in (m.group(0) for m in _FACT_RE.finditer(text)) if t not in _SPEECH_STOPWORDS}


def grounding_score(content: str, transcript: str) -> dict:
    """台本の固有名詞・数値のうち、章コンテンツに実在する割合。

    形態素解析なしの近似: 漢字連は前方一致の部分文字列でも「実在」とみなす
    (活用・複合語対策)。1 - score が捏造リスクの proxy。
    """
    terms = extract_fact_terms(transcript)
    if not terms:
        return {"score": 1.0, "unsupported": [], "total": 0}
    unsupported = [
        t for t in terms
        if t not in content and (len(t) < 3 or t[:2] not in content)
    ]
    score = 1 - len(unsupported) / len(terms)
    return {
        "score": round(score, 3),
        "unsupported": sorted(unsupported)[:20],
        "total": len(terms),
    }


def politeness_score(text: str) -> float:
    """文末の敬体率（です/ます調の一貫性）。"""
    sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if len(s.strip()) >= 5]
    if not sentences:
        return 0.0
    polite = sum(1 for s in sentences if POLITE_ENDINGS.search(s + "。"))
    return round(polite / len(sentences), 3)


# 合成報酬の重み。既定は手設計だが、報酬蒸留（scripts/distill_reward.py が
# 章の👍/👎から学習して data/rl/reward_weights.json を書く）で上書きできる。
# ゲート（sidecar）と最適化器は composite 経由でこの重みを共有する。
DEFAULT_REWARD_WEIGHTS = {
    "structure": 0.35,
    "grounding": 0.4,
    "politeness": 0.15,
    "length": 0.1,
}
REWARD_WEIGHTS_FILE_ENV = "REWARD_WEIGHTS_FILE"
_DEFAULT_WEIGHTS_PATH = "data/rl/reward_weights.json"
_weights_cache: dict | None = None


def load_reward_weights() -> dict:
    """蒸留済み重みがあれば読む（プロセス内キャッシュ、不正値は既定へフォールバック）。"""
    global _weights_cache
    if _weights_cache is not None:
        return _weights_cache
    import os

    path = Path(os.getenv(REWARD_WEIGHTS_FILE_ENV, _DEFAULT_WEIGHTS_PATH))
    weights = dict(DEFAULT_REWARD_WEIGHTS)
    try:
        if path.exists():
            data = json.loads(path.read_text())
            candidate = {k: float(data[k]) for k in DEFAULT_REWARD_WEIGHTS}
            total = sum(candidate.values())
            if total > 0 and all(v >= 0 for v in candidate.values()):
                weights = {k: round(v / total, 4) for k, v in candidate.items()}
    except Exception:  # noqa: BLE001 - bad weights must never break scoring
        weights = dict(DEFAULT_REWARD_WEIGHTS)
    _weights_cache = weights
    return weights


def _reset_weights_cache() -> None:
    """テスト用: 重みキャッシュを破棄する。"""
    global _weights_cache
    _weights_cache = None


@dataclass
class ChapterEval:
    chapter: str
    structure: float
    grounding: float
    politeness: float
    length_ratio: float
    unsupported_terms: list

    @property
    def composite(self) -> float:
        """総合報酬（ゲート・optimize_briefing と共有。重みは蒸留で更新可能）。"""
        w = load_reward_weights()
        return round(
            w["structure"] * self.structure
            + w["grounding"] * self.grounding
            + w["politeness"] * self.politeness
            # 台本が極端に薄い/水増しのときに減点（1.0〜8.0倍を許容帯とする）
            + w["length"] * (1.0 if 1.0 <= self.length_ratio <= 8.0 else 0.5),
            3,
        )


def evaluate_chapter(name: str, content: str, transcript: object) -> ChapterEval:
    text = transcript_text(transcript)
    st = structure_score(text)
    gr = grounding_score(content, text)
    return ChapterEval(
        chapter=name,
        structure=st["score"],
        grounding=gr["score"],
        politeness=politeness_score(text),
        length_ratio=round(len(text) / max(len(content), 1), 2),
        unsupported_terms=gr["unsupported"],
    )


# ---------------------------------------------------------------------------
# Model pricing (USD per 1M tokens, 2026-07) for the comparison report
# ---------------------------------------------------------------------------

PRICES = {
    "anthropic:claude-opus-4-8": (5.0, 25.0),
    "anthropic:claude-sonnet-5": (3.0, 15.0),
    "anthropic:claude-haiku-4-5": (1.0, 5.0),
    "deepseek:deepseek-v4-flash": (0.14, 0.28),
    "deepseek:deepseek-chat": (0.14, 0.28),  # legacy alias of v4-flash
    "google:gemini-3.1-flash-lite": (0.25, 1.5),
}


async def generate_with_model(provider: str, model: str, briefing: str, content: str) -> tuple[str, int, int]:
    """Generate a monologue script with one LLM call via esperanto.

    NOTE: これは実パイプライン(outline→transcript)の1コール近似。モデル間の
    「書きの品質」を同条件で比べるための計測用で、実運用の置き換えではない。
    """
    from esperanto.factory import AIFactory

    from open_notebook.ai.key_provider import provision_provider_keys

    await provision_provider_keys(provider)
    # Esperanto's default max_tokens truncates a full chapter script mid-way;
    # a Book Navigator monologue needs ~5-8k tokens of headroom.
    llm = AIFactory.create_language(
        model_name=model, provider=provider, config={"max_tokens": 8000}
    )
    prompt = (
        f"{briefing}\n\n---\n以下が章のテキストです。上の指示に従い、"
        f"一人語りの台本のみを出力してください。\n\n{content}"
    )
    response = await llm.achat_complete(messages=[{"role": "user", "content": prompt}])
    text = getattr(response, "content", None) or ""
    usage = getattr(response, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    return text, in_tok, out_tok


# ---------------------------------------------------------------------------
# Data access + report
# ---------------------------------------------------------------------------


async def load_chapters(audiobook_id: str, limit: int | None) -> list[dict]:
    from open_notebook.database.repository import repo_query

    rows = await repo_query(
        "SELECT chapter_index, chapter_title, content, transcript, briefing "
        "FROM episode WHERE type::string(audiobook) = $ab ORDER BY chapter_index",
        {"ab": audiobook_id},
    )
    return rows[:limit] if limit else rows


def print_eval_table(evals: list[ChapterEval]) -> None:
    print(f"{'chapter':<28} {'struct':>6} {'ground':>6} {'polite':>6} {'len':>5} {'REWARD':>6}")
    for e in evals:
        print(
            f"{e.chapter[:26]:<28} {e.structure:>6.2f} {e.grounding:>6.2f} "
            f"{e.politeness:>6.2f} {e.length_ratio:>5.1f} {e.composite:>6.2f}"
        )
        if e.unsupported_terms:
            print(f"    ⚠ 本文に無い語: {', '.join(e.unsupported_terms[:8])}")
    if evals:
        avg = sum(e.composite for e in evals) / len(evals)
        print(f"{'AVG':<28} {'':>6} {'':>6} {'':>6} {'':>5} {avg:>6.2f}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audiobook", required=True, help="audiobook:<id>")
    ap.add_argument("--chapters", type=int, help="最初のN章のみ")
    ap.add_argument("--compare", nargs="*", metavar="PROVIDER:MODEL",
                    help="各モデルで台本を生成して比較（LLMコスト発生）")
    ap.add_argument("--json", help="結果をJSONで書き出すパス")
    args = ap.parse_args()

    chapters = await load_chapters(args.audiobook, args.chapters)
    if not chapters:
        sys.exit(f"no chapters found for {args.audiobook}")

    results: dict = {"audiobook": args.audiobook, "existing": [], "compare": {}}

    print(f"\n=== 既存台本の採点 ({len(chapters)}章) ===")
    evals = [
        evaluate_chapter(
            f"ch{c.get('chapter_index')}:{c.get('chapter_title') or ''}",
            c.get("content") or "",
            c.get("transcript"),
        )
        for c in chapters
        if c.get("transcript")
    ]
    print_eval_table(evals)
    results["existing"] = [asdict(e) | {"composite": e.composite} for e in evals]

    for spec in args.compare or []:
        provider, _, model = spec.partition(":")
        print(f"\n=== {spec} で再生成して採点 ===")
        model_evals: list[ChapterEval] = []
        total_in = total_out = 0
        for c in chapters:
            content = c.get("content") or ""
            briefing = c.get("briefing") or ""
            try:
                text, in_tok, out_tok = await generate_with_model(
                    provider, model, briefing, content
                )
            except Exception as e:  # noqa: BLE001 - report and continue
                print(f"  ch{c.get('chapter_index')}: 生成失敗 {e}")
                continue
            total_in += in_tok
            total_out += out_tok
            model_evals.append(
                evaluate_chapter(f"ch{c.get('chapter_index')}", content, text)
            )
        print_eval_table(model_evals)
        in_price, out_price = PRICES.get(spec, (0.0, 0.0))
        cost = total_in / 1e6 * in_price + total_out / 1e6 * out_price
        print(f"  tokens: in={total_in} out={total_out}  概算コスト: ${cost:.4f}")
        results["compare"][spec] = {
            "evals": [asdict(e) | {"composite": e.composite} for e in model_evals],
            "tokens_in": total_in,
            "tokens_out": total_out,
            "cost_usd": round(cost, 4),
        }

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\nJSON written: {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
