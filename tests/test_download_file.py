"""download_file must always release the streaming HTTP response (socket),
on success AND on mid-download cancel. Network is mocked — no real requests."""
import os

import pytest

import downloader.download_file as dl


class FakeResp:
    def __init__(self, chunks, total):
        self.headers = {"Content-Length": str(total)}
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        for c in self._chunks:
            yield c

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


@pytest.fixture
def fake_get(monkeypatch):
    holder = {}

    def _install(chunks, total):
        resp = FakeResp(chunks, total)
        holder["resp"] = resp
        monkeypatch.setattr(dl.requests, "get", lambda *a, **k: resp)
        return resp
    return _install


def test_response_closed_on_success(tmp_path, fake_get):
    resp = fake_get([b"x" * 100], 100)
    path = dl.download_file("http://x/y.cbz", str(tmp_path), "Saga", "001", "2012")
    assert os.path.isfile(path)
    assert resp.closed is True


def test_response_closed_on_cancel(tmp_path, fake_get):
    resp = fake_get([b"x" * 10] * 64, 10 * 64)  # enough chunks to hit the cancel check
    with pytest.raises(dl.DownloadCancelled):
        dl.download_file("http://x/y.cbz", str(tmp_path), "Saga", "001", "2012",
                         is_cancelled=lambda: True)
    assert resp.closed is True
    # the .part scratch file must not be left behind
    assert not any(f.endswith(".part") for f in os.listdir(tmp_path))
