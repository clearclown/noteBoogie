"""Unit + integration tests for scripts/ingest_book.py (SuperBook ingestion)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.ingest_book import (
    caption_figure,
    chapter_index_for_page,
    ingest,
    rewrite_markdown_for_audio,
)

# ---------------------------------------------------------------------------
# chapter_index_for_page
# ---------------------------------------------------------------------------

CHAPTERS = [
    {"title": "はじめに", "page": 7},
    {"title": "第1章", "page": 16},
    {"title": "第2章", "page": 30},
]


def test_page_before_first_chapter_has_no_index():
    assert chapter_index_for_page(CHAPTERS, 1) is None
    assert chapter_index_for_page(CHAPTERS, 6) is None


def test_page_on_chapter_boundary_belongs_to_that_chapter():
    assert chapter_index_for_page(CHAPTERS, 7) == 0
    assert chapter_index_for_page(CHAPTERS, 16) == 1
    assert chapter_index_for_page(CHAPTERS, 30) == 2


def test_page_between_boundaries_belongs_to_previous_chapter():
    assert chapter_index_for_page(CHAPTERS, 15) == 0
    assert chapter_index_for_page(CHAPTERS, 29) == 1


def test_page_past_last_chapter_belongs_to_last():
    assert chapter_index_for_page(CHAPTERS, 999) == 2


def test_empty_chapters_yield_none():
    assert chapter_index_for_page([], 5) is None


# ---------------------------------------------------------------------------
# rewrite_markdown_for_audio
# ---------------------------------------------------------------------------


def test_captioned_image_becomes_marker():
    md = "前文\n![図](images/page_003_fig_001.png)\n後文"
    out = rewrite_markdown_for_audio(md, {"images/page_003_fig_001.png": "売上の推移"})
    assert "【図: 売上の推移】" in out
    assert "![" not in out


def test_uncaptioned_image_is_stripped_with_trailing_newline():
    md = "A\n![図](images/gone.png)\nB"
    out = rewrite_markdown_for_audio(md, {})
    assert out == "A\nB"


def test_alt_text_is_ignored_for_lookup():
    md = "![なんでもいい代替テキスト](images/x.png)"
    out = rewrite_markdown_for_audio(md, {"images/x.png": "キャプション"})
    assert out == "【図: キャプション】"


def test_multiple_images_mixed():
    md = "![a](i/1.png)\n本文\n![b](i/2.png)\n"
    out = rewrite_markdown_for_audio(md, {"i/2.png": "図2"})
    assert out == "本文\n【図: 図2】"


# ---------------------------------------------------------------------------
# caption_figure (anthropic client stubbed)
# ---------------------------------------------------------------------------


def _fake_client(response=None, error=None):
    client = MagicMock()
    if error is not None:
        client.messages.create.side_effect = error
    else:
        client.messages.create.return_value = response
    return client


def _response(stop_reason="end_turn", blocks=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=blocks
        if blocks is not None
        else [SimpleNamespace(type="text", text="  この図は矢印を示す。  ")],
    )


def test_caption_success_strips_whitespace(tmp_path):
    img = tmp_path / "fig.png"
    img.write_bytes(b"\x89PNGfake")
    client = _fake_client(response=_response())
    assert caption_figure(client, "m", img) == "この図は矢印を示す。"


def test_caption_refusal_returns_none(tmp_path):
    img = tmp_path / "fig.png"
    img.write_bytes(b"x")
    client = _fake_client(response=_response(stop_reason="refusal"))
    assert caption_figure(client, "m", img) is None


def test_caption_api_error_returns_none(tmp_path):
    img = tmp_path / "fig.png"
    img.write_bytes(b"x")
    client = _fake_client(error=RuntimeError("boom"))
    assert caption_figure(client, "m", img) is None


def test_caption_media_type_for_jpg(tmp_path):
    img = tmp_path / "fig.jpg"
    img.write_bytes(b"x")
    client = _fake_client(response=_response())
    caption_figure(client, "m", img)
    payload = client.messages.create.call_args.kwargs
    image_block = payload["messages"][0]["content"][0]
    assert image_block["source"]["media_type"] == "image/jpeg"


def test_caption_no_text_block_returns_none(tmp_path):
    img = tmp_path / "fig.png"
    img.write_bytes(b"x")
    client = _fake_client(
        response=_response(blocks=[SimpleNamespace(type="thinking", text="…")])
    )
    assert caption_figure(client, "m", img) is None


# ---------------------------------------------------------------------------
# ingest() orchestration (domain + repo mocked, filesystem real via tmp_path)
# ---------------------------------------------------------------------------


def _write_book_dir(tmp_path, figures=None, chapters=None, md_body=None):
    manifest = {
        "version": 1,
        "pages": 3,
        "text_direction": "vertical",
        "chapters": chapters
        if chapters is not None
        else [{"title": "第1章", "page": 1}, {"title": "第2章", "page": 3}],
        "figures": figures if figures is not None else [],
    }
    (tmp_path / "book_manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "本.md").write_text(
        md_body if md_body is not None else "# 第1章\n本文です。\n"
    )
    (tmp_path / "images").mkdir(exist_ok=True)
    return tmp_path


class _FakeRecord:
    def __init__(self, rid):
        self.id = rid
        self.save = AsyncMock()
        self.add_to_notebook = AsyncMock()
        self.vectorize = AsyncMock(return_value="command:job1")


@pytest.fixture
def wired(monkeypatch):
    """Patch every persistence surface ingest() touches; return the mocks."""
    import scripts.ingest_book as mod

    notebook = _FakeRecord("notebook:n1")
    source = _FakeRecord("source:s1")
    monkeypatch.setattr(mod, "Notebook", MagicMock(return_value=notebook))
    monkeypatch.setattr(mod, "Source", MagicMock(return_value=source))
    monkeypatch.setattr(mod, "Asset", MagicMock(side_effect=lambda **kw: kw))
    repo_insert = AsyncMock()
    monkeypatch.setattr(mod, "repo_insert", repo_insert)
    monkeypatch.setattr(
        mod, "repo_query", AsyncMock(return_value=[{"count": 0}])
    )
    monkeypatch.setattr(mod, "ensure_record_id", lambda x: x)
    return SimpleNamespace(
        notebook=notebook, source=source, repo_insert=repo_insert, mod=mod
    )


@pytest.mark.asyncio
async def test_ingest_missing_manifest_exits(tmp_path):
    with pytest.raises(SystemExit):
        await ingest(tmp_path, None, None, captions=False, caption_model="m")


@pytest.mark.asyncio
async def test_ingest_missing_md_exits(tmp_path):
    (tmp_path / "book_manifest.json").write_text("{}")
    with pytest.raises(SystemExit):
        await ingest(tmp_path, None, None, captions=False, caption_model="m")


@pytest.mark.asyncio
async def test_ingest_saves_source_and_figures(tmp_path, wired):
    figures = [
        {"path": "images/page_001_full.png", "page": 1, "kind": "full_page"},
        {"path": "images/page_003_fig_001.png", "page": 3, "kind": "figure"},
        {"path": "images/cover_001.png", "page": 1, "kind": "cover"},
    ]
    dirp = _write_book_dir(tmp_path, figures=figures)
    await ingest(dirp, None, "テスト本", captions=False, caption_model="m")

    wired.notebook.save.assert_awaited_once()
    wired.source.save.assert_awaited_once()
    wired.source.add_to_notebook.assert_awaited_once_with("notebook:n1")
    wired.source.vectorize.assert_awaited_once()

    # figure/cover は book_figure 化されるが、キャプションの付かない full_page は
    # 本文の写しでありギャラリーのノイズになるため除外される（66冊バッチで実測）。
    records = wired.repo_insert.call_args.args[1]
    assert wired.repo_insert.call_args.args[0] == "book_figure"
    assert len(records) == 2
    by_page = {r["path"]: r for r in records}
    assert str((dirp / "images/page_001_full.png").resolve()) not in by_page
    fig = by_page[str((dirp / "images/page_003_fig_001.png").resolve())]
    assert fig["chapter_index"] == 1  # page 3 -> 第2章 (index 1)
    assert fig["kind"] == "figure"
    assert fig["caption"] is None  # captions disabled


@pytest.mark.asyncio
async def test_ingest_captions_only_figure_and_full_page_kinds(
    tmp_path, wired, monkeypatch
):
    figures = [
        {"path": "images/cover_001.png", "page": 1, "kind": "cover"},
        {"path": "images/page_002_fig_001.png", "page": 2, "kind": "figure"},
        # テキストの無い full_page（本文ページの写し）は vision に送らない
        {"path": "images/page_003_full.png", "page": 3, "kind": "full_page"},
        # テキスト豊富な full_page はテキスト経路で要約される
        {"path": "images/page_004_full.png", "page": 4, "kind": "full_page",
         "text": "全ページ図のテキスト。" * 20},
        {"path": "images/missing.png", "page": 3, "kind": "figure"},
    ]
    dirp = _write_book_dir(
        tmp_path,
        figures=figures,
        md_body="# 第1章\n![図](images/page_002_fig_001.png)\n本文。\n",
    )
    # Only figures whose file exists reach the captioner.
    (dirp / "images/page_002_fig_001.png").write_bytes(b"x")
    (dirp / "images/page_003_full.png").write_bytes(b"x")
    (dirp / "images/page_004_full.png").write_bytes(b"x")

    captioned = []
    text_captioned = []

    def fake_caption(client, model, image_path, figure_text=None):
        captioned.append(image_path.name)
        return "図の説明"

    def fake_text_caption(client, model, figure_text):
        text_captioned.append(figure_text[:10])
        return "テキスト図の説明"

    monkeypatch.setattr(wired.mod, "caption_figure", fake_caption)
    monkeypatch.setattr(wired.mod, "caption_from_text", fake_text_caption)
    monkeypatch.setattr(
        "anthropic.Anthropic", MagicMock(), raising=False
    )

    await ingest(dirp, None, "本", captions=True, caption_model="m")

    # vision は真の図のみ。テキスト無し full_page は送られず、
    # テキスト豊富な full_page は低コストのテキスト経路へ
    assert captioned == ["page_002_fig_001.png"]
    assert len(text_captioned) == 1
    # The captioned image link in the md became a spoken marker on the Source.
    source_kwargs = wired.mod.Source.call_args.kwargs
    assert "【図: 図の説明】" in source_kwargs["full_text"]
    assert "![" not in source_kwargs["full_text"]


# ---------------------------------------------------------------------------
# is_blank_image (vision-cost guard)
# ---------------------------------------------------------------------------


def test_blank_and_content_images_are_distinguished(tmp_path):
    from PIL import Image, ImageDraw

    from scripts.ingest_book import is_blank_image

    blank = tmp_path / "blank.png"
    Image.new("L", (200, 300), color=250).save(blank)
    assert is_blank_image(blank) is True

    figure = tmp_path / "figure.png"
    im = Image.new("L", (200, 300), color=255)
    draw = ImageDraw.Draw(im)
    for y in range(0, 300, 20):
        draw.line([(10, y), (190, y)], fill=0, width=3)
    im.save(figure)
    assert is_blank_image(figure) is False


def test_unreadable_image_is_not_treated_as_blank(tmp_path):
    from scripts.ingest_book import is_blank_image

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert is_blank_image(broken) is False


# ---------------------------------------------------------------------------
# Figure-text reuse (vision-cost reduction via YomiToku in-figure OCR)
# ---------------------------------------------------------------------------


def test_text_dominant_figure_skips_vision(tmp_path, wired, monkeypatch):
    from scripts.ingest_book import TEXT_ONLY_CAPTION_THRESHOLD

    long_text = "図中の説明テキスト。" * 20  # >= threshold
    assert len(long_text) >= TEXT_ONLY_CAPTION_THRESHOLD
    figures = [
        {"path": "images/page_002_fig_001.png", "page": 2, "kind": "figure", "text": long_text},
        {"path": "images/page_003_fig_001.png", "page": 3, "kind": "figure", "text": "短い"},
    ]
    dirp = _write_book_dir(tmp_path, figures=figures)
    (dirp / "images/page_002_fig_001.png").write_bytes(b"x")
    (dirp / "images/page_003_fig_001.png").write_bytes(b"x")

    vision_calls, text_calls = [], []
    monkeypatch.setattr(
        wired.mod, "is_blank_image", lambda *a, **k: False
    )
    monkeypatch.setattr(
        wired.mod,
        "caption_figure",
        lambda client, model, img, figure_text=None: vision_calls.append(
            (img.name, figure_text)
        )
        or "vision説明",
    )
    monkeypatch.setattr(
        wired.mod,
        "caption_from_text",
        lambda client, model, text: text_calls.append(text) or "テキスト説明",
    )
    monkeypatch.setattr("anthropic.Anthropic", MagicMock(), raising=False)

    import asyncio

    asyncio.run(
        wired.mod.ingest(dirp, None, "本", captions=True, caption_model="m")
    )

    # Long in-figure text -> text-only path (no image tokens); short text ->
    # vision WITH the text passed as a hint.
    assert text_calls == [long_text]
    assert vision_calls == [("page_003_fig_001.png", "短い")]


def test_caption_from_text_handles_refusal_and_success():
    from scripts.ingest_book import caption_from_text

    ok = _fake_client(response=_response())
    assert caption_from_text(ok, "m", "図内の文字") == "この図は矢印を示す。"
    # The prompt embeds the figure text, no image block is sent.
    payload = ok.messages.create.call_args.kwargs
    assert "図内の文字" in payload["messages"][0]["content"]

    refusal = _fake_client(response=_response(stop_reason="refusal"))
    assert caption_from_text(refusal, "m", "x") is None


def test_vision_prompt_includes_figure_text_hint(tmp_path):
    from scripts.ingest_book import caption_figure

    img = tmp_path / "fig.png"
    img.write_bytes(b"x")
    client = _fake_client(response=_response())
    caption_figure(client, "m", img, figure_text="売上推移 2020-2024")
    payload = client.messages.create.call_args.kwargs
    text_block = payload["messages"][0]["content"][1]["text"]
    assert "売上推移 2020-2024" in text_block
