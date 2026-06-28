"""Cache-first Metron: a series page reads the issue cache and never blocks on
Metron, even when the cache is stale. Metron is only hit on a cold (empty) cache
and by the nightly refresh (which skips ended series)."""
import time
from datetime import datetime, timedelta, timezone

import pytest

import metadata.metron_client as mc
import web.app as appmod
import web.metron_refresh as refresh
from web.models import MetronIssueCache, Series


def _series(db, **kw):
    s = Series(publisher="Image", series_name="Saga", year=2012, metron_series_id=101, **kw)
    db.add(s); db.commit()
    return s


def _cache_issue(db, series_mid, number, cached_at):
    db.add(MetronIssueCache(metron_id=int(number) + series_mid * 1000, series_id=series_mid,
                            number=number, name=f"Chapter {number}",
                            cover_date="2012-03-14", store_date="2012-03-14", cached_at=cached_at))
    db.commit()


def test_stale_cache_served_without_metron(client, db, monkeypatch):
    s = _series(db)
    old = datetime.utcnow() - timedelta(days=999)  # well past the 7-day TTL
    _cache_issue(db, 101, "1", old)
    _cache_issue(db, 101, "2", old)

    def _boom(*a, **k):
        raise AssertionError("Metron must not be called on a page open with a non-empty cache")
    monkeypatch.setattr(mc, "get", _boom)

    r = client.get(f"/api/series/{s.id}/issues")
    assert r.status_code == 200
    body = r.json()
    assert body["has_metron"] is True
    assert {i["number"] for i in body["regular"]} == {"1", "2"}


def test_empty_cache_fetches_once(client, db, monkeypatch):
    s = _series(db)
    calls = {"n": 0}

    class _Resp:
        def json(self):
            # No "name" → if title hydration ran it'd make a per-issue detail call.
            return {"next": None, "results": [
                {"id": 5001, "number": "1", "cover_date": "2012-03-14", "store_date": "2012-03-14", "image": ""},
            ]}

    def _fake(*a, **k):
        calls["n"] += 1
        return _Resp()
    monkeypatch.setattr(mc, "get", _fake)

    r = client.get(f"/api/series/{s.id}/issues")
    assert r.status_code == 200
    assert [i["number"] for i in r.json()["regular"]] == ["1"]
    assert calls["n"] == 1  # exactly the list fetch — no per-issue title hydration on a passive load
    assert db.query(MetronIssueCache).filter(MetronIssueCache.series_id == 101).count() == 1


def test_nightly_refresh_skips_ended_series(db, monkeypatch):
    active = Series(publisher="Image", series_name="Saga", year=2012, metron_series_id=101, status="Ongoing")
    ended = Series(publisher="DC", series_name="Watchmen", year=1986, metron_series_id=202, status="Completed")
    db.add_all([active, ended]); db.commit()

    seen: list[int] = []
    monkeypatch.setattr(appmod, "_refresh_one_series", lambda s, db, **k: seen.append(s.metron_series_id) or True)

    assert refresh.run_refresh(force=True, skip_titles=False, only_active=True) is True
    # wait for the background thread to finish
    for _ in range(100):
        if not refresh.get_status()["running"]:
            break
        time.sleep(0.05)
    assert seen == [101]  # only the non-ended series was refreshed
