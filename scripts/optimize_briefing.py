"""RLベースの briefing（台本プロンプト）最適化ハーネス。

ユーザー定義のRL要素をそのまま実装に落としている:
  行動:   briefing のテキスト編集（optimizer LLM が編集案を K 個生成）
  状態:   現在の briefing + 過去の (briefing, 報酬, コスト) 履歴
  報酬:   eval_transcript の品質合成スコア − λ×消費トークン
  方策:   LLM-as-optimizer（OPRO系）。世代ごとに「高報酬/低報酬の対比」を
          注入した最適化プロンプトで編集案を出させ、ビーム的に最良を残す。
          （方策勾配への差し替えは、この行動空間定義の上で後段の課題）

Usage:
    uv run --env-file .env python scripts/optimize_briefing.py \
        --audiobook audiobook:xxx --chapters 3 \
        --generations 3 --beam 3 [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_transcript import evaluate_chapter, generate_with_model  # noqa: E402

OPTIMIZER_MODEL = ("anthropic", "claude-sonnet-5")

# ---------------------------------------------------------------------------
# Reward / state (pure, unit-tested)
# ---------------------------------------------------------------------------


@dataclass
class Trial:
    """One evaluated briefing: the state history entry."""

    briefing: str
    reward: float
    quality: float
    tokens_out: int
    rationale: str = ""


@dataclass
class Budget:
    """Hard token ceiling for the whole optimization run."""

    max_total_tokens: int
    spent: int = 0

    def charge(self, tokens: int) -> None:
        self.spent += tokens

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.max_total_tokens


def compose_reward(quality: float, tokens_out: int, token_penalty: float) -> float:
    """報酬 = 品質 − λ×(出力トークン/1万)。

    ユーザー提案「タスク成功までの消費トークン数」を λ 項として組み込む:
    同品質なら短い台本（＝安く・聴きやすい）が勝つ。
    """
    return round(quality - token_penalty * (tokens_out / 10_000), 4)


def build_optimizer_prompt(current: Trial, history: list[Trial], k: int) -> str:
    """状態（履歴の高低対比）→ 行動（編集案K個）を引き出すプロンプト。"""
    ranked = sorted(history, key=lambda t: t.reward, reverse=True)
    lines = [
        "あなたはプロンプト最適化器です。日本語ビジネス書の章を「耳だけで理解できる"
        "一人語り台本」へ変換する briefing を改善します。",
        "",
        "報酬 = 0.35*構成遵守 + 0.4*本文グラウンディング + 0.15*敬体一貫性 + 0.1*長さ適正"
        " − λ*出力トークン。特に『本文に無い固有名詞・数値を語らない』ことが最大の弱点です。",
        "",
        "## これまでの試行（報酬順）",
    ]
    for t in ranked[:3]:
        lines.append(f"- 報酬 {t.reward:.3f} (品質 {t.quality:.3f}, {t.tokens_out}tok): "
                     f"{t.briefing[:160]}…")
    if len(ranked) > 3:
        worst = ranked[-1]
        lines.append(f"- [最低] 報酬 {worst.reward:.3f}: {worst.briefing[:120]}…")
    lines += [
        "",
        "## 現在の briefing 全文",
        current.briefing,
        "",
        f"## 指示",
        f"上の履歴から何が報酬を上げるかを推測し、briefing の編集案を{k}個作成してください。"
        "各案は具体的な編集（文の追加・削除・言い換え）を1〜2箇所に絞ること。"
        "構成（課題→3要点→アクションプラン）の核は維持すること。",
        "次のJSONだけを出力: "
        '[{"rationale": "編集意図", "briefing": "編集後の全文"}, …]',
    ]
    return "\n".join(lines)


def parse_candidates(text: str) -> list[dict]:
    """optimizer 出力から候補JSONを取り出す（コードフェンス許容）。"""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [
        i for i in items
        if isinstance(i, dict) and isinstance(i.get("briefing"), str) and i["briefing"].strip()
    ]


# ---------------------------------------------------------------------------
# Evaluation loop
# ---------------------------------------------------------------------------


async def evaluate_briefing(
    briefing: str,
    chapters: list[dict],
    gen_model: tuple[str, str],
    budget: Budget,
    token_penalty: float,
    rationale: str = "",
) -> Trial:
    """1つの briefing を評価セット全章で試し、平均報酬を返す。"""
    qualities: list[float] = []
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
    quality = round(sum(qualities) / max(len(qualities), 1), 4)
    per_chapter_out = tokens_out // max(len(chapters), 1)
    return Trial(
        briefing=briefing,
        quality=quality,
        tokens_out=per_chapter_out,
        reward=compose_reward(quality, per_chapter_out, token_penalty),
        rationale=rationale,
    )


async def optimize(args: argparse.Namespace) -> None:
    from open_notebook.database.repository import repo_query

    # SurrealDB quirk: an ORDER BY expression must appear in the projection.
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
    baseline_briefing = profile[0]["default_briefing"]

    gen_provider, _, gen_model = args.gen_model.partition(":")
    budget = Budget(max_total_tokens=args.max_tokens)
    history: list[Trial] = []

    print(f"評価セット: {len(chapters)}章 / 世代 {args.generations} / ビーム {args.beam} "
          f"/ 予算 {args.max_tokens} tok / 生成 {args.gen_model}")

    best = await evaluate_briefing(
        baseline_briefing, chapters, (gen_provider, gen_model), budget, args.token_penalty,
        rationale="baseline",
    )
    history.append(best)
    print(f"gen0 baseline: reward={best.reward:.3f} quality={best.quality:.3f} "
          f"out={best.tokens_out}tok/章")

    for gen in range(1, args.generations + 1):
        if budget.exhausted:
            print(f"予算超過で停止 ({budget.spent} tok)")
            break
        prompt = build_optimizer_prompt(best, history, args.beam)
        text, in_tok, out_tok = await generate_with_model(
            OPTIMIZER_MODEL[0], OPTIMIZER_MODEL[1], "", prompt
        )
        budget.charge(in_tok + out_tok)
        candidates = parse_candidates(text)
        if not candidates:
            print(f"gen{gen}: 候補のパースに失敗（スキップ）")
            continue
        for i, cand in enumerate(candidates[: args.beam]):
            if budget.exhausted:
                break
            trial = await evaluate_briefing(
                cand["briefing"], chapters, (gen_provider, gen_model), budget,
                args.token_penalty, rationale=cand.get("rationale", ""),
            )
            history.append(trial)
            marker = " ← BEST" if trial.reward > best.reward else ""
            print(f"gen{gen}.{i}: reward={trial.reward:.3f} quality={trial.quality:.3f} "
                  f"({trial.rationale[:60]}){marker}")
            if trial.reward > best.reward:
                best = trial

    print("\n=== 結果 ===")
    print(f"baseline reward: {history[0].reward:.3f} → best: {best.reward:.3f}")
    print(f"総消費トークン: {budget.spent}")
    if args.out:
        Path(args.out).write_text(json.dumps(
            {"best_briefing": best.briefing, "best_reward": best.reward,
             "history": [{"reward": t.reward, "quality": t.quality,
                          "rationale": t.rationale} for t in history]},
            ensure_ascii=False, indent=2))
        print(f"レポート: {args.out}")

    if args.apply and best.rationale != "baseline":
        await repo_query(
            "UPDATE episode_profile SET default_briefing = $b WHERE name = $n",
            {"b": best.briefing, "n": args.profile},
        )
        print(f"episode_profile '{args.profile}' の briefing を更新しました")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audiobook", required=True)
    ap.add_argument("--profile", default="book_navigator")
    ap.add_argument("--chapters", type=int, default=3, help="評価に使う章数（長い順）")
    ap.add_argument("--generations", type=int, default=3)
    ap.add_argument("--beam", type=int, default=3, help="世代あたりの編集案数")
    ap.add_argument("--gen-model", default="anthropic:claude-sonnet-5",
                    help="台本生成モデル provider:model")
    ap.add_argument("--token-penalty", type=float, default=0.05,
                    help="報酬のλ（出力1万tokあたりの減点）")
    ap.add_argument("--max-tokens", type=int, default=300_000,
                    help="最適化全体のトークン予算（超過で停止）")
    ap.add_argument("--out", help="結果JSONの出力先")
    ap.add_argument("--apply", action="store_true",
                    help="最良briefingを episode_profile に書き戻す")
    asyncio.run(optimize(ap.parse_args()))


if __name__ == "__main__":
    main()
