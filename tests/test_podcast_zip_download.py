"""ZIP一括ダウンロードエンドポイント download_episodes_zip の検証。

サーバ側ZIP化（複数章を1ファイルで保存・スマホ配慮）が、正しいメンバー名で
mp3 を束ね、空要求/音声なしを適切に弾くことを固定する。
"""

import zipfile

import pytest

from api.routers.podcasts import EpisodesDownloadRequest, download_episodes_zip


def _make_audio(tmp_path, monkeypatch, uuid, name="final.mp3", data=b"ID3fakeaudio"):
    """PODCASTS_FOLDER 配下に実ファイルを置き、相対パスを返す。"""
    monkeypatch.setattr(
        "open_notebook.podcasts.audio_paths.PODCASTS_FOLDER", str(tmp_path)
    )
    d = tmp_path / "episodes" / uuid / "audio"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return f"episodes/{uuid}/audio/{name}"


def _install_repo(monkeypatch, rows_by_id):
    """endpoint内 `from ... import repo_query` を差し替える。"""

    async def fake_query(q, params=None):
        eid = (params or {}).get("id")
        return rows_by_id.get(eid, [])

    monkeypatch.setattr(
        "open_notebook.database.repository.repo_query", fake_query
    )


@pytest.mark.asyncio
async def test_zip_bundles_two_episodes_with_clean_names(tmp_path, monkeypatch):
    af1 = _make_audio(tmp_path, monkeypatch, "u1", "a.mp3", b"AAAA")
    af2 = _make_audio(tmp_path, monkeypatch, "u2", "b.mp3", b"BBBB")
    _install_repo(
        monkeypatch,
        {
            "episode:1": [
                {"chapter_index": 1, "chapter_title": "第1章 序論", "audio_file": af1, "book": "サンプル書籍"}
            ],
            "episode:2": [
                {"chapter_index": 2, "chapter_title": "第2章 管理会計", "audio_file": af2, "book": "サンプル書籍"}
            ],
        },
    )
    resp = await download_episodes_zip(
        EpisodesDownloadRequest(episode_ids=["episode:1", "episode:2"], zip_name="サンプル書籍")
    )
    assert resp.media_type == "application/zip"
    with zipfile.ZipFile(resp.path) as zf:
        names = sorted(zf.namelist())
    assert names == ["サンプル書籍_01_序論.mp3", "サンプル書籍_02_管理会計.mp3"]


@pytest.mark.asyncio
async def test_empty_episode_ids_returns_422(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await download_episodes_zip(EpisodesDownloadRequest(episode_ids=[]))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_no_downloadable_audio_returns_404(tmp_path, monkeypatch):
    # audio_file が None の章しかない → 404
    monkeypatch.setattr(
        "open_notebook.podcasts.audio_paths.PODCASTS_FOLDER", str(tmp_path)
    )
    _install_repo(
        monkeypatch,
        {"episode:x": [{"chapter_index": 0, "chapter_title": "序", "audio_file": None, "book": "本"}]},
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await download_episodes_zip(EpisodesDownloadRequest(episode_ids=["episode:x"]))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_member_names_are_deduped(tmp_path, monkeypatch):
    # 同じ本・同じ章番号・同じ章名 → メンバー名衝突 → _1 で回避
    af1 = _make_audio(tmp_path, monkeypatch, "d1", "a.mp3", b"AAAA")
    af2 = _make_audio(tmp_path, monkeypatch, "d2", "b.mp3", b"BBBB")

    def row(af):
        return [{"chapter_index": 1, "chapter_title": "第1章 同名", "audio_file": af, "book": "本"}]

    _install_repo(monkeypatch, {"episode:1": row(af1), "episode:2": row(af2)})
    resp = await download_episodes_zip(
        EpisodesDownloadRequest(episode_ids=["episode:1", "episode:2"], zip_name="本")
    )
    with zipfile.ZipFile(resp.path) as zf:
        names = zf.namelist()
    assert len(names) == 2
    assert len(set(names)) == 2  # 名前が衝突していない
