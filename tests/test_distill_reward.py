"""RL段階3: 報酬モデル蒸留（👍/👎 → 合成重み）のテスト。"""

import argparse
import json
from unittest.mock import AsyncMock, patch

import pytest

from scripts.distill_reward import (
    FEATURES,
    accuracy,
    distill,
    featurize,
    fit_logistic,
    to_reward_weights,
)
from scripts.eval_transcript import (
    DEFAULT_REWARD_WEIGHTS,
    _reset_weights_cache,
    load_reward_weights,
)


@pytest.fixture(autouse=True)
def reset_weights():
    _reset_weights_cache()
    yield
    _reset_weights_cache()


# --- eval_transcript の重み読み込み -----------------------------------------


def test_default_weights_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("REWARD_WEIGHTS_FILE", str(tmp_path / "nope.json"))
    assert load_reward_weights() == DEFAULT_REWARD_WEIGHTS


def test_distilled_weights_override_and_normalize(monkeypatch, tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps(
        {"structure": 2.0, "grounding": 6.0, "politeness": 1.0, "length": 1.0}
    ))
    monkeypatch.setenv("REWARD_WEIGHTS_FILE", str(path))
    w = load_reward_weights()
    assert w["grounding"] == 0.6  # 正規化される
    assert abs(sum(w.values()) - 1.0) < 1e-6


def test_invalid_weights_fall_back(monkeypatch, tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({"structure": -1.0, "grounding": 0.5}))  # 負+キー欠落
    monkeypatch.setenv("REWARD_WEIGHTS_FILE", str(path))
    assert load_reward_weights() == DEFAULT_REWARD_WEIGHTS


def test_composite_uses_distilled_weights(monkeypatch, tmp_path):
    from scripts.eval_transcript import ChapterEval

    path = tmp_path / "weights.json"
    # グラウンディングだけを見る報酬モデル
    path.write_text(json.dumps(
        {"structure": 0.0, "grounding": 1.0, "politeness": 0.0, "length": 0.0}
    ))
    monkeypatch.setenv("REWARD_WEIGHTS_FILE", str(path))
    e = ChapterEval("ch", structure=0.0, grounding=0.8, politeness=0.0,
                    length_ratio=100.0, unsupported_terms=[])
    assert e.composite == 0.8


# --- 学習の純関数 -----------------------------------------------------------


def test_fit_logistic_separable_data():
    # grounding（index 1）だけがラベルを決める合成データ
    features = [[0.5, 0.9, 0.5, 1.0], [0.5, 0.95, 0.6, 1.0],
                [0.5, 0.2, 0.5, 1.0], [0.5, 0.1, 0.6, 1.0]] * 5
    labels = [1, 1, 0, 0] * 5
    w, b = fit_logistic(features, labels)
    assert accuracy(w, b, features, labels) == 1.0
    assert w[1] == max(w)  # grounding が最大係数


def test_to_reward_weights_clips_and_normalizes():
    w = to_reward_weights([1.0, 3.0, -2.0, 0.0])
    assert w == {"structure": 0.25, "grounding": 0.75, "politeness": 0.0, "length": 0.0}
    # 全て非正 → 学習失敗として既定へ
    assert to_reward_weights([-1.0, -0.5, 0.0, -2.0]) == DEFAULT_REWARD_WEIGHTS


def test_featurize_maps_length_band():
    content = "仮説思考とは結論から考える技術である。" * 20
    good = featurize(content, content[:200])
    assert len(good) == len(FEATURES)
    assert good[3] == 0.5  # length_ratio < 1.0 → 減点側


# --- distill フロー ----------------------------------------------------------


def make_args(tmp_path, apply=True, min_samples=4):
    return argparse.Namespace(
        min_samples=min_samples, out=str(tmp_path / "weights.json"), apply=apply
    )


@pytest.mark.asyncio
async def test_distill_requires_min_samples(tmp_path, capsys):
    with patch(
        "scripts.distill_reward.fetch_labeled_episodes",
        new=AsyncMock(return_value=[{"feedback": "up"}]),
    ):
        await distill(make_args(tmp_path, min_samples=10))
    assert "不足" in capsys.readouterr().out
    assert not (tmp_path / "weights.json").exists()


@pytest.mark.asyncio
async def test_distill_requires_both_labels(tmp_path, capsys):
    rows = [{"feedback": "up", "content": "c", "transcript": "t"}] * 5
    with patch(
        "scripts.distill_reward.fetch_labeled_episodes", new=AsyncMock(return_value=rows)
    ):
        await distill(make_args(tmp_path, min_samples=5))
    assert "片側のみ" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_distill_writes_weights_file(tmp_path, capsys):
    rows = (
        [{"feedback": "up", "content": "good", "transcript": "t"}] * 6
        + [{"feedback": "down", "content": "bad", "transcript": "t"}] * 6
    )

    def fake_featurize(content, transcript):
        # up は高グラウンディング、down は低グラウンディングの世界
        return [0.5, 0.9, 0.8, 1.0] if content == "good" else [0.5, 0.2, 0.8, 1.0]

    with (
        patch(
            "scripts.distill_reward.fetch_labeled_episodes",
            new=AsyncMock(return_value=rows),
        ),
        patch("scripts.distill_reward.featurize", new=fake_featurize),
    ):
        await distill(make_args(tmp_path, min_samples=10))

    data = json.loads((tmp_path / "weights.json").read_text())
    assert data["fitted_on"] == 12
    assert data["accuracy"] == 1.0
    # 人間の好みを分けた grounding に重みが寄る
    assert data["grounding"] == max(data[k] for k in FEATURES)
