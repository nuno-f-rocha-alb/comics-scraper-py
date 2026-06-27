"""download-staging: finished comics are built in a staging folder, then landed
in the library atomically (hidden dot-temp + os.replace) so Komga never sees a
partial/untagged file. Network + metadata mocked; all dirs are temp."""
import os

import pytest

import util
import downloader.process_downloaded_comic as proc
import downloader.download_file as dl
import web.worker as worker


def test_install_moves_and_lands_atomically(tmp_path):
    staging = tmp_path / "staging"
    dest = tmp_path / "Series (2020)"
    staging.mkdir()
    staged = staging / "Saga #001 (2020).cbz"
    staged.write_bytes(b"comic")

    final = util.install_to_library(str(staged), str(dest))

    assert final == str(dest / "Saga #001 (2020).cbz")
    assert os.path.isfile(final)
    assert not staged.exists()                       # source removed
    # only the finished file appears — no dot-temp / partial left behind
    assert os.listdir(dest) == ["Saga #001 (2020).cbz"]


def test_install_overwrites_existing(tmp_path):
    staging = tmp_path / "staging"
    dest = tmp_path / "dest"
    staging.mkdir()
    dest.mkdir()
    (dest / "Saga #001 (2020).cbz").write_bytes(b"old")
    staged = staging / "Saga #001 (2020).cbz"
    staged.write_bytes(b"new")

    final = util.install_to_library(str(staged), str(dest))

    assert open(final, "rb").read() == b"new"        # re-download overwrites
    assert not staged.exists()


def test_install_across_filesystems(monkeypatch, tmp_path):
    # Force the cross-device path: replace shutil.move with a copy+unlink so the
    # rename fast-path can't hide a bug in our dot-temp + os.replace landing.
    import shutil as _shutil

    def _copy_move(src, dst):
        _shutil.copyfile(src, dst)
        os.remove(src)
        return dst

    monkeypatch.setattr(util.shutil, "move", _copy_move)

    staging = tmp_path / "staging"
    dest = tmp_path / "dest"
    staging.mkdir()
    staged = staging / "Saga #002 (2020).cbz"
    staged.write_bytes(b"comic")

    final = util.install_to_library(str(staged), str(dest))

    assert os.path.isfile(final)
    assert not staged.exists()
    assert os.listdir(dest) == ["Saga #002 (2020).cbz"]  # no .tmp left


def test_process_returns_final_cbz_path(monkeypatch, tmp_path):
    cbr = tmp_path / "Saga #001 (2020).cbr"
    cbr.write_bytes(b"rar")

    def fake_convert(path):
        cbz = os.path.splitext(path)[0] + ".cbz"
        os.rename(path, cbz)
        return cbz

    monkeypatch.setattr(proc, "convert_cbr_to_cbz", fake_convert)
    monkeypatch.setattr(proc, "get_comic_metadata", lambda *a, **k: None)

    entry = ("Image", "Saga", "2020")
    returned = proc.process_downloaded_comic(entry, str(cbr), "1")

    assert returned.endswith("Saga #001 (2020).cbz")
    assert os.path.isfile(returned)


class _FakeResp:
    def __init__(self, data):
        self.headers = {"Content-Length": str(len(data))}
        self._data = data

    def raise_for_status(self): pass
    def iter_content(self, chunk_size=8192): yield self._data
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Series:
    def to_scraper_tuple(self):
        # (publisher, series, year, cv_id, annual_cv_id, metron_id, search_name)
        return ("Image", "Saga", "2020", None, None, None, None)


def test_worker_installs_to_library_not_staging(monkeypatch, tmp_path):
    monkeypatch.setattr(util, "COMICS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(dl.requests, "get", lambda *a, **k: _FakeResp(b"comic-bytes"))
    monkeypatch.setattr(
        "downloader.get_comic_download_url.get_comic_download_url",
        lambda url: "http://x/Saga.cbz",
    )
    monkeypatch.setattr(proc, "get_comic_metadata", lambda *a, **k: None)

    name = worker._download_issue(_Series(), "1", post_url="http://getcomics.org/p")

    series_dir = os.path.join(str(tmp_path), "Image", "Saga (2020)")
    assert name == "Saga #001 (2020).cbz"
    assert os.path.isfile(os.path.join(series_dir, name))
    # nothing left in staging
    staging = os.path.join(str(tmp_path), util.STAGING_SUBDIR)
    assert os.listdir(staging) == []
