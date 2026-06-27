"""_ensure_cover_cached must not cache a *failed* Metron fetch as a permanent
"no cover" result. image_url="" means "tried, genuinely nothing"; a transient
failure must stay retryable (row absent / image_url None)."""
import metadata.metron_client as mc
import web.app as appmod
from web.models import MetronCache


def test_failed_fetch_is_not_cached(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("metron unreachable")
    monkeypatch.setattr(mc, "get", boom)

    out = appmod._ensure_cover_cached(12345, db)
    assert out is None
    row = db.get(MetronCache, 12345)
    # must remain retryable — not poisoned with image_url=""
    assert row is None or row.image_url is None


def test_success_caches_image(db, metron_get):
    metron_get({"name": "Saga", "image": "http://img/saga.jpg",
                "publisher": {"name": "Image"}, "series_type": {"name": "Ongoing"},
                "year_began": 2012, "issue_count": 66, "cv_id": 1})
    out = appmod._ensure_cover_cached(100, db)
    assert out == "http://img/saga.jpg"
    assert db.get(MetronCache, 100).image_url == "http://img/saga.jpg"


def test_fallback_failure_is_not_cached(db, monkeypatch):
    # primary /series/ succeeds but has no image; the /issue/ cover fallback
    # then fails → must stay retryable, not cached as image_url="".
    calls = {"n": 0}

    class _Resp:
        def json(self):
            return {"name": "Y", "image": "", "publisher": {}, "series_type": {}}

    def _get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp()           # primary series fetch ok, no image
        raise RuntimeError("rate limited")  # fallback issue fetch fails
    monkeypatch.setattr(mc, "get", _get)

    out = appmod._ensure_cover_cached(300, db)
    assert out is None
    row = db.get(MetronCache, 300)
    assert row is None or row.image_url is None   # retryable, not poisoned


def test_no_image_caches_empty_sentinel(db, metron_get):
    # series has no image AND the issue-fallback returns nothing: a real
    # "tried, nothing" — cache "" so we don't refetch every page load.
    metron_get([{"name": "X", "image": "", "publisher": {}, "series_type": {}},
                {"results": []}])
    out = appmod._ensure_cover_cached(200, db)
    assert out is None
    assert db.get(MetronCache, 200).image_url == ""
