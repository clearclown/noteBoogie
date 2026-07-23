"""Verify a local Ollama LLM generates the Book Navigator monologue script.

Uses the same provider abstraction (esperanto) the app/sidecar use, with the
Book Navigator briefing, against a fully local Ollama model. No API key, no cloud.

Usage:
    uv run python scripts/verify_local_llm.py --model gpt-oss:20b
"""

import argparse
import os
import time

# The structured mentor-monologue briefing (mirrors migration 16's book_navigator).
BRIEFING = (
    "あなたは優秀なビジネスメンターです。与えられた章のテキストを解析し、疲れた"
    "ビジネスパーソンが耳だけで聴いて100%理解できるよう、一人語り（モノローグ）で"
    "喋り口調の台本を作ってください。必ず次の3部構成で話してください。"
    "1. この章が解決するビジネス上の問題（「この章は、〜という悩みに答えます」と切り出す）。"
    "2. 重要な3つのコンセプト（「1つ目は〜」「2つ目は〜」「3つ目は〜」と番号を声に出して"
    "数える。各コンセプトは最初に結論を一言で述べてから説明する）。"
    "3. 明日からそのまま真似できるアクションプラン（手順を1ステップずつ、具体的に）。"
    "ルール: 専門用語は避け平易な言葉に言い換える。句読点を多めに使い聴き取りやすい間を作る。"
)

SAMPLE_CHAPTER = (
    "# 第一章 時間管理の基本\n"
    "忙しいビジネスパーソンほど、優先順位付けが重要です。緊急度と重要度で四象限に分け、"
    "重要だが緊急でない仕事（自己投資、計画、関係構築）に意識的に時間を割り当てましょう。"
    "緊急なだけの仕事に追われ続けると、本当に成果を生む活動が後回しになります。"
)


def main(model: str, base_url: str) -> None:
    # esperanto's Ollama provider reads OLLAMA_API_BASE (falls back to localhost).
    os.environ.setdefault("OLLAMA_API_BASE", base_url)
    from esperanto import AIFactory

    print(f"creating local language model: ollama/{model} @ {base_url}")
    llm = AIFactory.create_language("ollama", model, config={"temperature": 0.6})

    messages = [
        {"role": "system", "content": BRIEFING},
        {"role": "user", "content": f"次の章を音声台本にしてください。\n\n{SAMPLE_CHAPTER}"},
    ]

    t0 = time.time()
    # Non-streaming call; narrow the ChatCompletion | stream union for mypy.
    resp = llm.chat_complete(messages)
    elapsed = time.time() - t0

    if not hasattr(resp, "choices"):
        raise SystemExit("unexpected streaming response from ollama")
    msg = resp.choices[0].message  # type: ignore[union-attr]
    # gpt-oss emits <think> reasoning; cleaned_content is the spoken script.
    script = (msg.cleaned_content or msg.content or "").strip()

    print(f"\n--- generated in {elapsed:.1f}s ---\n")
    print(script)

    # Lightweight structural assertions (the Book Navigator 3-part shape).
    ok = all(k in script for k in ["1つ目", "2つ目", "3つ目"])
    print("\n--- check ---")
    print(f"length: {len(script)} chars")
    print(f"three-concept markers present: {ok}")
    if not ok:
        raise SystemExit("WARNING: 3-part structure markers not found in output")
    print("OK: local LLM produced a structured Book Navigator monologue.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("--base-url", default="http://localhost:11434")
    args = ap.parse_args()
    main(args.model, args.base_url)
