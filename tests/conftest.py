"""Test harness for the FastAPI backend.

Runs prod-faithfully inside the python:3.12 test image (see Dockerfile.test).
Every external boundary is replaced with a local fake:
  - DB     -> a throwaway SQLite file under a temp dir
  - comics -> a temp dir (COMICS_BASE_DIR)
  - worker -> stubbed so the app's lifespan doesn't spawn download threads
  - Metron -> mocked per-test via the `metron_get` fixture (no network)

Env MUST be set before any `web.*` import, because web/database.py builds the
engine and web/app.py reads COMICS_BASE_DIR at import time.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="comics-test-")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["COMICS_BASE_DIR"] = os.path.join(_TMP, "comics")
os.environ.setdefault("METRON_USER", "test")
os.environ.setdefault("METRON_PASS", "test")
os.makedirs(os.environ["COMICS_BASE_DIR"], exist_ok=True)

import pytest

# Stub the download worker so TestClient's lifespan startup is inert.
import web.worker as _worker
_worker.start = lambda *a, **k: None

from fastapi.testclient import TestClient  # noqa: E402
import web.app as appmod  # noqa: E402
from web.database import Base, SessionLocal, engine, init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db()
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    """Each test starts with empty tables."""
    yield
    with SessionLocal() as s:
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def client():
    with TestClient(appmod.app) as c:
        yield c


@pytest.fixture
def comic_file():
    """Create a .cbz/.cbr file inside a series' local folder (real path that
    _series_dir resolves to), so the overview's filesystem scan sees it."""
    def _make(series, name: str):
        folder = appmod._series_dir(series)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w") as fh:
            fh.write("x")
        return path
    return _make


@pytest.fixture
def metron_get(monkeypatch):
    """Replace metadata.metron_client.get with a fake returning canned JSON.
    Usage: metron_get([{...page1...}, {...page2...}]) or metron_get({...})."""
    def _install(payloads):
        seq = payloads if isinstance(payloads, list) else [payloads]
        calls = iter(seq)

        class _Resp:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        def _fake_get(*args, **kwargs):
            try:
                return _Resp(next(calls))
            except StopIteration:
                return _Resp(seq[-1] if seq else {})

        import metadata.metron_client as mc
        monkeypatch.setattr(mc, "get", _fake_get)
        return _fake_get
    return _install
