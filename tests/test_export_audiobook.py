"""Unit tests for scripts/export_audiobook.py."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts.export_audiobook import export, safe_filename


class TestSafeFilename:
    def test_strips_forbidden_and_running_header_noise(self):
        assert safe_filename('第1部 | ケース面接入門編') == "第1部 ケース面接入門編"
        assert safe_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"
        assert safe_filename("第7章 ·") == "第7章"

    def test_empty_falls_back(self):
        assert safe_filename("|| ・") == "無題"

    def test_length_cap(self):
        assert len(safe_filename("あ" * 100)) == 60


@pytest.mark.asyncio
async def test_export_writes_ordered_files_and_playlist(tmp_path, monkeypatch):
    podcasts = tmp_path / "podcasts"
    (podcasts / "episodes/e1/audio").mkdir(parents=True)
    (podcasts / "episodes/e1/audio/e1.mp3").write_bytes(b"mp3-1")
    (podcasts / "episodes/e2/audio").mkdir(parents=True)
    (podcasts / "episodes/e2/audio/e2.mp3").write_bytes(b"mp3-2")
    monkeypatch.setattr("open_notebook.config.PODCASTS_FOLDER", str(podcasts))

    async def fake_query(q, binds=None):
        if "FROM audiobook" in q:
            return [{"name": "テストの本"}]
        return [
            {"chapter_index": 0, "chapter_title": "序章", "audio_file": "episodes/e1/audio/e1.mp3"},
            {"chapter_index": 1, "chapter_title": "第1章 | 本論", "audio_file": "episodes/e2/audio/e2.mp3"},
            {"chapter_index": 2, "chapter_title": "生成中", "audio_file": None},
        ]

    monkeypatch.setattr("open_notebook.database.repository.repo_query", fake_query)

    out_dir = await export("audiobook:a", tmp_path / "audiobooks", link=False)
    assert out_dir == tmp_path / "audiobooks/テストの本"
    names = sorted(p.name for p in out_dir.glob("*.mp3"))
    assert names == ["00_序章.mp3", "01_第1章 本論.mp3"]
    assert (out_dir / "00_序章.mp3").read_bytes() == b"mp3-1"

    playlist = (out_dir / "playlist.m3u8").read_text()
    assert playlist.splitlines() == ["#EXTM3U", "00_序章.mp3", "01_第1章 本論.mp3"]


@pytest.mark.asyncio
async def test_export_missing_audiobook_exits(monkeypatch):
    monkeypatch.setattr(
        "open_notebook.database.repository.repo_query", AsyncMock(return_value=[])
    )
    with pytest.raises(SystemExit):
        await export("audiobook:none", Path("/tmp"), link=False)
