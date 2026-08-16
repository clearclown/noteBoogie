"""ダウンロード名 <本タイトル>_<章番号(ゼロ埋め)>_<章名>.mp3 の組み立て検証。

章配信エンドポイントが返す Content-Disposition のファイル名が、UUID ではなく
人が読める形になること（第N章の二重表記を排し、前付けは00）を固定する。
"""

import pytest

from api.routers.podcasts import (
    _download_name_from_row,
    _episode_download_filename,
    _sanitize_filename_component,
    _strip_chapter_prefix,
)


def test_download_name_from_row_builds_zip_member():
    row = {
        "chapter_index": 4,
        "chapter_title": "第4章 品質と改善",
        "book": "サンプル書籍",
    }
    assert _download_name_from_row(row) == "サンプル書籍_04_品質と改善.mp3"


def test_download_name_from_row_front_matter_zero():
    row = {"chapter_index": 0, "chapter_title": "サンプル書籍", "book": "サンプル書籍"}
    assert _download_name_from_row(row) == "サンプル書籍_00_サンプル書籍.mp3"


def test_strip_chapter_prefix_removes_leading_chapter_marker():
    assert _strip_chapter_prefix("第4章 品質と改善") == "品質と改善"
    assert _strip_chapter_prefix("第10章 まとめ") == "まとめ"
    assert _strip_chapter_prefix("第1章：序論") == "序論"
    # 章マーカーが無い前付け等はそのまま
    assert _strip_chapter_prefix("サンプル書籍") == "サンプル書籍"


def test_sanitize_filename_component_removes_fs_unsafe_chars():
    assert _sanitize_filename_component("コーポレート/変革:日本?") == "コーポレート変革日本"
    assert _sanitize_filename_component("  空白　詰め  ") == "空白 詰め"


@pytest.mark.asyncio
async def test_episode_download_filename_builds_human_readable(monkeypatch):
    import api.routers.podcasts as mod

    async def fake_query(q, params):
        return [
            {
                "chapter_index": 4,
                "chapter_title": "第4章 品質と改善",
                "book": "サンプル書籍",
            }
        ]

    monkeypatch.setattr(mod, "repo_query", fake_query, raising=False)
    # repo_query は関数内 import なので、モジュール属性で差し替える
    import open_notebook.database.repository as repo

    monkeypatch.setattr(repo, "repo_query", fake_query)

    name = await _episode_download_filename("episode:x", "fallback.mp3")
    assert name == "サンプル書籍_04_品質と改善.mp3"


@pytest.mark.asyncio
async def test_episode_download_filename_front_matter_is_zero(monkeypatch):
    import open_notebook.database.repository as repo

    async def fake_query(q, params):
        return [
            {"chapter_index": 0, "chapter_title": "サンプル書籍", "book": "サンプル書籍"}
        ]

    monkeypatch.setattr(repo, "repo_query", fake_query)
    name = await _episode_download_filename("episode:x", "fallback.mp3")
    assert name == "サンプル書籍_00_サンプル書籍.mp3"


@pytest.mark.asyncio
async def test_episode_download_filename_falls_back_on_no_row(monkeypatch):
    import open_notebook.database.repository as repo

    async def fake_query(q, params):
        return []

    monkeypatch.setattr(repo, "repo_query", fake_query)
    assert await _episode_download_filename("episode:x", "uuid.mp3") == "uuid.mp3"


@pytest.mark.asyncio
async def test_episode_download_filename_falls_back_on_error(monkeypatch):
    import open_notebook.database.repository as repo

    async def boom(q, params):
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "repo_query", boom)
    assert await _episode_download_filename("episode:x", "uuid.mp3") == "uuid.mp3"
