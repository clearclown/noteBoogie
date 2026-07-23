"""RL段階3: 報酬モデル蒸留 — 章の👍/👎から合成報酬の重みを学習する。

人手シグナル（episode.feedback、フロントの👍/👎）を教師に、自動4指標
（構成遵守・グラウンディング・敬体・長さ適正）のロジスティック回帰を
素の勾配降下で当て、正規化した重みを data/rl/reward_weights.json に書く。
eval_transcript.composite がこのファイルを読むため、**ゲート（sidecar）と
プロンプト最適化器の報酬が人間の好みに追随する**ようになる。

Usage:
    uv run --env-file .env python scripts/distill_reward.py [--min-samples 10] [--apply]
    （--apply なしはレポートのみ。データ不足時は現状維持で終了）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_transcript import (  # noqa: E402
    DEFAULT_REWARD_WEIGHTS,
    evaluate_chapter,
    transcript_text,
)

FEATURES = ["structure", "grounding", "politeness", "length"]
DEFAULT_WEIGHTS_PATH = Path("data/rl/reward_weights.json")


def featurize(content: str, transcript: object) -> list[float]:
    """1エピソードを自動4指標の特徴ベクトルへ（composite と同じ素材）。"""
    e = evaluate_chapter("distill", content, transcript_text(transcript))
    length_ok = 1.0 if 1.0 <= e.length_ratio <= 8.0 else 0.5
    return [e.structure, e.grounding, e.politeness, length_ok]


def fit_logistic(
    features: list[list[float]],
    labels: list[int],
    epochs: int = 2000,
    lr: float = 0.5,
) -> tuple[list[float], float]:
    """素朴なロジスティック回帰（依存ライブラリなし）。

    Returns (weights[4], bias)。標本が少ない前提なので正則化は弱め（L2 1e-3）。
    """
    n_features = len(FEATURES)
    w = [0.0] * n_features
    b = 0.0
    n = len(features)
    l2 = 1e-3
    for _ in range(epochs):
        grad_w = [0.0] * n_features
        grad_b = 0.0
        for x, y in zip(features, labels):
            z = sum(wi * xi for wi, xi in zip(w, x)) + b
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            err = p - y
            for j in range(n_features):
                grad_w[j] += err * x[j]
            grad_b += err
        for j in range(n_features):
            w[j] -= lr * (grad_w[j] / n + l2 * w[j])
        b -= lr * grad_b / n
    return w, b


def predict(weights: list[float], bias: float, x: list[float]) -> float:
    z = sum(wi * xi for wi, xi in zip(weights, x)) + bias
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


def accuracy(weights: list[float], bias: float, features: list[list[float]], labels: list[int]) -> float:
    if not labels:
        return 0.0
    correct = sum(
        1 for x, y in zip(features, labels) if (predict(weights, bias, x) >= 0.5) == (y == 1)
    )
    return round(correct / len(labels), 3)


def to_reward_weights(raw: list[float]) -> dict:
    """回帰係数 → composite 用の非負・正規化重み。

    負係数（人間の評価と逆相関）は 0 に落とす。全て非正なら学習失敗として
    既定重みへフォールバック（人手データが指標と無相関なケースの安全弁）。
    """
    clipped = [max(0.0, v) for v in raw]
    total = sum(clipped)
    if total <= 0:
        return dict(DEFAULT_REWARD_WEIGHTS)
    return {name: round(v / total, 4) for name, v in zip(FEATURES, clipped)}


async def fetch_labeled_episodes() -> list[dict]:
    """👍/👎 が付いたエピソードの (feedback, content, transcript) を取る。"""
    from open_notebook.database.repository import repo_query

    return await repo_query(
        "SELECT feedback, content, transcript FROM episode "
        "WHERE feedback != NONE AND content != NONE AND transcript != NONE"
    )


async def distill(args: argparse.Namespace) -> None:
    rows = await fetch_labeled_episodes()
    if len(rows) < args.min_samples:
        print(
            f"人手フィードバックが不足しています: {len(rows)} 件 "
            f"(必要 {args.min_samples})。オーディオブックの章に👍/👎を付けてから再実行してください。"
        )
        return

    features: list[list[float]] = []
    labels: list[int] = []
    for row in rows:
        features.append(featurize(row.get("content") or "", row.get("transcript")))
        labels.append(1 if row.get("feedback") == "up" else 0)

    ups = sum(labels)
    if ups == 0 or ups == len(labels):
        print(f"ラベルが片側のみ（up={ups}/{len(labels)}）のため学習できません。")
        return

    raw_weights, bias = fit_logistic(features, labels)
    acc = accuracy(raw_weights, bias, features, labels)
    distilled = to_reward_weights(raw_weights)

    print(f"標本: {len(labels)} 件 (up={ups}, down={len(labels) - ups}) / 学習精度: {acc}")
    print(f"{'指標':<12}{'既定':>8}{'蒸留後':>10}")
    for name in FEATURES:
        print(f"{name:<12}{DEFAULT_REWARD_WEIGHTS[name]:>8.3f}{distilled[name]:>10.4f}")

    if args.apply:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {**distilled, "fitted_on": len(labels), "accuracy": acc},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"重みを書き出しました: {out}")
        print("以後のゲート採点と最適化器の報酬にこの重みが使われます。")
    else:
        print("（--apply で反映。反映後は sidecar 再起動で有効化）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-samples", type=int, default=10)
    ap.add_argument("--out", default=str(DEFAULT_WEIGHTS_PATH))
    ap.add_argument("--apply", action="store_true", help="重みファイルを書き出す")
    asyncio.run(distill(ap.parse_args()))


if __name__ == "__main__":
    main()
