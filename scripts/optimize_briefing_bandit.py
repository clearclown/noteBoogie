"""RL段階2: 行動の離散化 + Thompson sampling bandit による briefing 最適化。

段階1（optimize_briefing.py、自由編集の OPRO 系）との違い:
  行動空間: 自由編集 → **離散オペレータ集合**（add_rule / remove_sentence /
            rephrase_sentence / reorder / set_length_hint）
  方策:     optimizer LLM 任せ → **Thompson sampling**（オペレータごとに
            Beta(α,β) を持ち、成功=報酬改善で更新。学習状態は
            data/rl/bandit_state.json に永続化され、実行を跨いで賢くなる）
  ログ:     (状態ハッシュ, 行動, 報酬前後, 成否) を data/rl/bandit_log.jsonl に
            追記 — 後段の方策勾配（小型LMのLoRA微調整）の学習データになる

オペレータの**パラメータ**（どの文を消すか等）は instantiation LLM が埋めるが、
「どの種類の編集をするか」の選択が bandit の学習対象。

Usage:
    uv run --env-file .env python scripts/optimize_briefing_bandit.py \
        --audiobook audiobook:xxx --chapters 3 --steps 8 [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_transcript import evaluate_chapter, generate_with_model  # noqa: E402
from scripts.optimize_briefing import Budget, compose_reward  # noqa: E402

INSTANTIATION_MODEL = ("anthropic", "claude-sonnet-5")
STATE_PATH = Path("data/rl/bandit_state.json")
LOG_PATH = Path("data/rl/bandit_log.jsonl")

# ---------------------------------------------------------------------------
# 行動空間（離散オペレータ）
# ---------------------------------------------------------------------------

OPERATORS: dict[str, str] = {
    "add_rule": (
        "弱点指標に対応する新しいルールを**1文だけ**追加してください。"
        "既存の文は変更しないこと。"
    ),
    "remove_sentence": (
        "報酬に寄与していない冗長な文を**1文だけ**削除してください。"
        "構成（課題→3要点→アクションプラン）と捏造禁止ルールは残すこと。"
    ),
    "rephrase_sentence": (
        "曖昧・弱い表現の文を**1文だけ**、より具体的で断定的な指示に書き換えてください。"
    ),
    "reorder": (
        "文の順序を**1箇所だけ**入れ替えてください。最重要ルールは"
        "briefing の末尾に近いほど守られやすい点を考慮すること。"
    ),
    "set_length_hint": "(決定的オペレータ: LLM不使用)",
}

LENGTH_HINTS = {
    "shorter": "台本は章の要点を絞り、冗長な繰り返しを避けて簡潔にまとめてください。",
    "longer": "台本は章の論点を漏らさず、具体例を添えて十分な分量で語ってください。",
}
_LENGTH_HINT_RE = re.compile(r"台本[^。]*(簡潔にまとめて|十分な分量で語って)[^。]*。")


def apply_set_length_hint(briefing: str, mean_length_ratio: float) -> str | None:
    """長さ比の実測から長さ指示を決定的に付け替える（許容帯なら何もしない）。"""
    if 1.0 <= mean_length_ratio <= 8.0:
        return None
    hint = LENGTH_HINTS["shorter" if mean_length_ratio > 8.0 else "longer"]
    stripped = _LENGTH_HINT_RE.sub("", briefing).rstrip()
    return f"{stripped}{hint}"


def build_instantiation_prompt(operator: str, briefing: str, weakness: str) -> str:
    return "\n".join(
        [
            "あなたはプロンプト編集器です。以下の briefing に対して、指定された種類の編集を"
            "**1箇所だけ**適用してください。",
            "",
            f"## 編集の種類\n{OPERATORS[operator]}",
            f"## 現在の弱点（自動採点より）\n{weakness}",
            "## 現在の briefing 全文",
            briefing,
            "",
            '次のJSONだけを出力: {"rationale": "編集意図", "briefing": "編集後の全文"}',
        ]
    )


def parse_single_edit(text: str) -> dict | None:
    """instantiation 出力から {rationale, briefing} を取り出す。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        item = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if isinstance(item, dict) and isinstance(item.get("briefing"), str) and item["briefing"].strip():
        return item
    return None


# ---------------------------------------------------------------------------
# 方策: Thompson sampling（オペレータごとの Beta 分布）
# ---------------------------------------------------------------------------


@dataclass
class ThompsonBandit:
    """成功=報酬改善 の Beta(α,β) をオペレータごとに保持する。"""

    arms: dict[str, list[float]] = field(
        default_factory=lambda: {name: [1.0, 1.0] for name in OPERATORS}
    )
    rng: random.Random = field(default_factory=random.Random)

    def sample(self) -> str:
        draws = {
            name: self.rng.betavariate(alpha, beta)
            for name, (alpha, beta) in self.arms.items()
        }
        return max(draws, key=lambda name: draws[name])

    def update(self, name: str, success: bool) -> None:
        alpha, beta = self.arms[name]
        self.arms[name] = [alpha + 1.0, beta] if success else [alpha, beta + 1.0]

    def to_dict(self) -> dict:
        return {"arms": self.arms}

    @classmethod
    def from_dict(cls, data: dict, rng: random.Random | None = None) -> "ThompsonBandit":
        bandit = cls(rng=rng or random.Random())
        stored = data.get("arms") or {}
        # 新オペレータ追加に耐える: 既知のものだけ引き継ぐ
        for name in bandit.arms:
            if name in stored and len(stored[name]) == 2:
                bandit.arms[name] = [float(stored[name][0]), float(stored[name][1])]
        return bandit


def load_bandit(path: Path, rng: random.Random | None = None) -> ThompsonBandit:
    try:
        if path.exists():
            return ThompsonBandit.from_dict(json.loads(path.read_text()), rng=rng)
    except Exception:  # noqa: BLE001 - corrupt state -> start fresh
        pass
    return ThompsonBandit(rng=rng or random.Random())


def save_bandit(bandit: ThompsonBandit, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bandit.to_dict(), indent=2))


def log_episode(path: Path, record: dict) -> None:
    """(状態, 行動, 報酬) を JSONL に追記 — 方策勾配（段階2後半）の学習データ。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 評価（optimize_briefing と同じ素材 + 長さ比を保持）
# ---------------------------------------------------------------------------


@dataclass
class BanditTrial:
    briefing: str
    reward: float
    quality: float
    mean_length_ratio: float
    weakness: str


async def evaluate_briefing_for_bandit(
    briefing: str,
    chapters: list[dict],
    gen_model: tuple[str, str],
    budget: Budget,
    token_penalty: float,
) -> BanditTrial:
    qualities: list[float] = []
    ratios: list[float] = []
    worst: tuple[float, str] = (10.0, "")
    tokens_out = 0
    for c in chapters:
        content = c.get("content") or ""
        text, in_tok, out_tok = await generate_with_model(
            gen_model[0], gen_model[1], briefing, content
        )
        budget.charge(in_tok + out_tok)
        tokens_out += out_tok
        e = evaluate_chapter(f"ch{c.get('chapter_index')}", content, text)
        qualities.append(e.composite)
        ratios.append(e.length_ratio)
        for metric in ("structure", "grounding", "politeness"):
            value = getattr(e, metric)
            if value < worst[0]:
                worst = (value, f"{metric}={value:.2f}")
    quality = round(sum(qualities) / max(len(qualities), 1), 4)
    per_chapter_out = tokens_out // max(len(chapters), 1)
    return BanditTrial(
        briefing=briefing,
        quality=quality,
        reward=compose_reward(quality, per_chapter_out, token_penalty),
        mean_length_ratio=round(sum(ratios) / max(len(ratios), 1), 2),
        weakness=f"最弱指標: {worst[1]}",
    )


# ---------------------------------------------------------------------------
# メインループ
# ---------------------------------------------------------------------------


async def optimize(args: argparse.Namespace) -> None:
    from open_notebook.database.repository import repo_query

    rows = await repo_query(
        "SELECT chapter_index, content, string::len(content) AS content_len "
        "FROM episode "
        "WHERE type::string(audiobook) = $ab AND string::len(content) > 500 "
        "ORDER BY content_len DESC",
        {"ab": args.audiobook},
    )
    chapters = rows[: args.chapters]
    if not chapters:
        sys.exit("評価に使える章がありません（content > 500字が必要）")

    profile = await repo_query(
        "SELECT default_briefing FROM episode_profile WHERE name = $n",
        {"n": args.profile},
    )
    if not profile or not profile[0].get("default_briefing"):
        sys.exit(f"episode profile '{args.profile}' の briefing が見つかりません")

    gen_provider, _, gen_model = args.gen_model.partition(":")
    budget = Budget(max_total_tokens=args.max_tokens)
    bandit = load_bandit(Path(args.state))

    current = await evaluate_briefing_for_bandit(
        profile[0]["default_briefing"], chapters, (gen_provider, gen_model),
        budget, args.token_penalty,
    )
    print(f"baseline: reward={current.reward:.3f} ({current.weakness}) "
          f"/ arms={ {k: v for k, v in bandit.arms.items()} }")

    for step in range(1, args.steps + 1):
        if budget.exhausted:
            print(f"予算超過で停止 ({budget.spent} tok)")
            break
        operator = bandit.sample()

        if operator == "set_length_hint":
            edited = apply_set_length_hint(current.briefing, current.mean_length_ratio)
            rationale = f"length_ratio={current.mean_length_ratio} に基づく決定的付替え"
            if edited is None:
                # 許容帯内では無効な行動 — 失敗として学習させ、無限選択を防ぐ
                bandit.update(operator, success=False)
                print(f"step{step}: {operator} → 長さは許容帯内（スキップ・失敗計上）")
                continue
        else:
            prompt = build_instantiation_prompt(operator, current.briefing, current.weakness)
            text, in_tok, out_tok = await generate_with_model(
                INSTANTIATION_MODEL[0], INSTANTIATION_MODEL[1], "", prompt
            )
            budget.charge(in_tok + out_tok)
            edit = parse_single_edit(text)
            if edit is None:
                bandit.update(operator, success=False)
                print(f"step{step}: {operator} → 編集のパースに失敗（失敗計上）")
                continue
            edited = edit["briefing"]
            rationale = edit.get("rationale", "")

        trial = await evaluate_briefing_for_bandit(
            edited, chapters, (gen_provider, gen_model), budget, args.token_penalty
        )
        success = trial.reward > current.reward
        bandit.update(operator, success)
        log_episode(
            Path(args.log),
            {
                "ts": int(time.time()),
                "state": hashlib.sha256(current.briefing.encode()).hexdigest()[:12],
                "action": operator,
                "rationale": rationale[:120],
                "reward_before": current.reward,
                "reward_after": trial.reward,
                "success": success,
            },
        )
        marker = " ← 採用" if success else ""
        print(f"step{step}: {operator} reward {current.reward:.3f}→{trial.reward:.3f}"
              f" ({rationale[:50]}){marker}")
        if success:
            current = trial

    save_bandit(bandit, Path(args.state))
    print("\n=== 結果 ===")
    print(f"final reward={current.reward:.3f} / 総消費 {budget.spent} tok")
    print("arms(α,β):", json.dumps(bandit.arms, ensure_ascii=False))

    if args.apply:
        await repo_query(
            "UPDATE episode_profile SET default_briefing = $b WHERE name = $n",
            {"b": current.briefing, "n": args.profile},
        )
        print(f"episode_profile '{args.profile}' の briefing を更新しました")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audiobook", required=True)
    ap.add_argument("--profile", default="book_navigator")
    ap.add_argument("--chapters", type=int, default=3)
    ap.add_argument("--steps", type=int, default=8, help="bandit の試行回数")
    ap.add_argument("--gen-model", default="anthropic:claude-haiku-4-5",
                    help="台本生成モデル provider:model（評価コスト重視で既定haiku）")
    ap.add_argument("--token-penalty", type=float, default=0.05)
    ap.add_argument("--max-tokens", type=int, default=300_000)
    ap.add_argument("--state", default=str(STATE_PATH), help="bandit 状態の永続化先")
    ap.add_argument("--log", default=str(LOG_PATH), help="(状態,行動,報酬) ログの追記先")
    ap.add_argument("--apply", action="store_true")
    asyncio.run(optimize(ap.parse_args()))


if __name__ == "__main__":
    main()
