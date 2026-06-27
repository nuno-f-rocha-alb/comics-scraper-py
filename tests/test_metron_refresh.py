"""§4 Metron overhaul — the unified per-series refresh (_refresh_one_series),
its TTL gate, cv_id discovery, the background runner's already-running guard,
and the endpoint wiring. Metron is mocked; no network."""
from datetime import datetime, timedelta

import pytest

import metadata.metron_client as mc
import web.app as appmod
import web.metron_refresh as mr
from metadata.metron_client import RateLimitedError
from web.models import Series


_DETAIL = {
    "image": "http://c/cover.jpg", "issue_count": 10,
    "status": {"name": "Ongoing"}, "series_type": {"name": "Ongoing"},
    "year_end": None, "cv_id": 1,
}
_ISSUES = {"results": [{"number": "1", "store_date": "2020-01-01", "image": "", "name": []}],
           "next": None}


def _mk(db, **kw):
    s = Series(publisher="P", series_name="S", year=2020, **kw)
    db.add(s)
    db.commit()
    return s


def test_force_refresh_updates_meta_and_stamps(db, metron_get):
    metron_get([_DETAIL, _ISSUES])
    s = _mk(db, metron_series_id=1)
    assert appmod._refresh_one_series(s, db, force=True) is True
    assert s.total_issues == 10
    assert s.status == "Ongoing"
    assert s.cover_image_url == "http://c/cover.jpg"
    assert s.metron_refreshed_at is not None


def test_ttl_skips_detail_when_fresh(db, metron_get):
    # Only an issue-list payload is provided; if the detail call fired it would
    # be a wrong-shaped response. The skip path returns False and leaves the
    # stamp + meta untouched.
    metron_get([_ISSUES])
    stamp = datetime.utcnow() - timedelta(hours=1)
    s = _mk(db, metron_series_id=1, total_issues=99, metron_refreshed_at=stamp)
    assert appmod._refresh_one_series(s, db, force=False) is False
    assert s.total_issues == 99
    assert s.metron_refreshed_at == stamp


def test_force_bypasses_fresh_ttl(db, metron_get):
    metron_get([_DETAIL, _ISSUES])
    s = _mk(db, metron_series_id=1, total_issues=99,
            metron_refreshed_at=datetime.utcnow())
    assert appmod._refresh_one_series(s, db, force=True) is True
    assert s.total_issues == 10


def test_stale_refreshes_without_force(db, metron_get):
    metron_get([_DETAIL, _ISSUES])
    old = datetime.utcnow() - timedelta(days=30)
    s = _mk(db, metron_series_id=1, total_issues=99, metron_refreshed_at=old)
    assert appmod._refresh_one_series(s, db, force=False) is True
    assert s.total_issues == 10
    assert s.metron_refreshed_at > old


def test_cv_id_discovers_metron_id(db, metron_get):
    metron_get([{"results": [{"id": 777}]}, _DETAIL, _ISSUES])
    s = _mk(db, comicvine_volume_id=555)
    assert appmod._refresh_one_series(s, db, force=True) is True
    assert s.metron_series_id == 777


def test_no_metron_id_and_no_cv_id_is_noop(db, metron_get):
    metron_get([{}])
    s = _mk(db)
    assert appmod._refresh_one_series(s, db, force=True) is False


def test_rate_limit_propagates(db, monkeypatch):
    # A RateLimitedError on the detail call must propagate so the worker stops,
    # not get swallowed into a silent "False".
    def boom(*a, **k):
        raise RateLimitedError(60)
    monkeypatch.setattr(mc, "get", boom)
    s = _mk(db, metron_series_id=1)
    with pytest.raises(RateLimitedError):
        appmod._refresh_one_series(s, db, force=True)


def test_run_refresh_guards_against_concurrent(monkeypatch):
    monkeypatch.setattr(mr, "_running", True)
    assert mr.run_refresh() is False


def test_status_endpoint_shape(client):
    r = client.get("/api/metron/refresh/status")
    assert r.status_code == 200
    body = r.json()
    for k in ("running", "last_refresh_at", "last_error", "progress", "last_result"):
        assert k in body


def test_refresh_endpoint_kicks_job(client, monkeypatch):
    # Wiring only — don't spawn a real thread that would hit the network.
    monkeypatch.setattr(mr, "run_refresh", lambda *a, **k: True)
    r = client.post("/api/metron/refresh")
    assert r.status_code == 200
    assert r.json()["started"] is True


def test_removed_routes_are_gone(client):
    # POST handlers gone — unreachable regardless of catch-all fallback routing.
    assert client.post("/api/metron/cache/refresh").status_code in {404, 405}
    assert client.post("/api/sync-covers").status_code in {404, 405}
