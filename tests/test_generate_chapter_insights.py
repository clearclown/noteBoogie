"""Unit tests for scripts/generate_chapter_insights.py (LLM/DB mocked)."""

import argparse
from unittest.mock import AsyncMock

import pytest

import scripts.generate_chapter_insights as gi


def test_prompt_embeds_content_and_bans_fabrication():
    prompt = gi.build_insight_prompt("章の本文です。")
    assert "章の本文です。" in prompt
    assert "創作しない" in prompt


@pytest.mark.asyncio
async def test_load_chapters_filters_thin_ones(monkeypatch):
    async def fake_query(q, binds=None):
        if "FROM audiobook" in q:
            return [{"source_id": "source:s1"}]
        return [
            {"chapter_index": 0, "chapter_title": "目次", "content": "薄", "content_len": 30},
            {"chapter_index": 1, "chapter_title": "第1章", "content": "本文" * 600, "content_len": 1200},
        ]

    monkeypatch.setattr("open_notebook.database.repository.repo_query", fake_query)
    source_id, chapters = await gi.load_substantial_chapters("audiobook:a", 1000)
    assert source_id == "source:s1"
    assert [c["chapter_index"] for c in chapters] == [1]


@pytest.mark.asyncio
async def test_missing_audiobook_exits(monkeypatch):
    monkeypatch.setattr(
        "open_notebook.database.repository.repo_query", AsyncMock(return_value=[])
    )
    with pytest.raises(SystemExit):
        await gi.load_substantial_chapters("audiobook:none", 1000)


@pytest.mark.asyncio
async def test_run_creates_insights_and_survives_failures(monkeypatch, capsys):
    async def fake_query(q, binds=None):
        if "FROM audiobook" in q:
            return [{"source_id": "source:s1"}]
        return [
            {"chapter_index": 1, "chapter_title": "第1章", "content": "A" * 1500, "content_len": 1500},
            {"chapter_index": 2, "chapter_title": "第2章", "content": "B" * 1500, "content_len": 1500},
            {"chapter_index": 3, "chapter_title": "第3章", "content": "C" * 1500, "content_len": 1500},
        ]

    monkeypatch.setattr("open_notebook.database.repository.repo_query", fake_query)

    calls = []

    async def fake_generate(provider, model, briefing, prompt):
        if "B" * 100 in prompt:
            raise RuntimeError("LLM flake")
        calls.append(model)
        return "要約テキスト", 100, 200

    monkeypatch.setattr(gi, "generate_with_model", fake_generate)

    source = AsyncMock()
    monkeypatch.setattr(
        "open_notebook.domain.notebook.Source.get", AsyncMock(return_value=source)
    )

    args = argparse.Namespace(
        audiobook="audiobook:a",
        model="anthropic:claude-haiku-4-5",
        min_chars=1000,
        dry_run=False,
    )
    await gi.run(args)

    # Chapter 2 failed but 1 and 3 were still inserted.
    assert source.add_insight.await_count == 2
    first = source.add_insight.await_args_list[0].args
    assert first[0] == "章インサイト: 第1章"
    assert first[1] == "要約テキスト"
    assert "ch2 生成失敗" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_dry_run_touches_nothing(monkeypatch):
    async def fake_query(q, binds=None):
        if "FROM audiobook" in q:
            return [{"source_id": "source:s1"}]
        return [{"chapter_index": 1, "chapter_title": "第1章", "content": "x" * 1500, "content_len": 1500}]

    monkeypatch.setattr("open_notebook.database.repository.repo_query", fake_query)
    generate = AsyncMock()
    monkeypatch.setattr(gi, "generate_with_model", generate)

    args = argparse.Namespace(
        audiobook="audiobook:a", model="a:m", min_chars=1000, dry_run=True
    )
    await gi.run(args)
    generate.assert_not_awaited()
