"""convert_cbr_to_cbz must delete the source CBR only on a fully clean
conversion; a partial conversion (any entry failed) keeps the original so no
pages are lost. rarfile is mocked — no real archives / unrar binary needed."""
import os

import pytest

import util


class _FakeFile:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


class _Entry:
    def __init__(self, name, fail=False):
        self.filename = name
        self.fail = fail


class _FakeRar:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def infolist(self):
        return self._entries

    def open(self, entry):
        if entry.fail:
            raise OSError("corrupt entry")
        return _FakeFile(b"page-bytes")


@pytest.fixture
def cbr(tmp_path):
    p = tmp_path / "Comic #001.cbr"
    p.write_bytes(b"fake-rar")
    return str(p)


def _patch_rar(monkeypatch, entries):
    monkeypatch.setattr(util.rarfile, "RarFile", lambda path: _FakeRar(entries))


def test_clean_conversion_deletes_source(monkeypatch, cbr):
    _patch_rar(monkeypatch, [_Entry("01.jpg"), _Entry("02.jpg")])
    cbz = util.convert_cbr_to_cbz(cbr)
    assert os.path.isfile(cbz)
    assert not os.path.exists(cbr)        # clean → source removed


def test_partial_conversion_keeps_source(monkeypatch, cbr):
    _patch_rar(monkeypatch, [_Entry("01.jpg"), _Entry("02.jpg", fail=True)])
    util.convert_cbr_to_cbz(cbr)
    assert os.path.exists(cbr)            # partial → original preserved
