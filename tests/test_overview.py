"""End-to-end of _series_overview via GET /api/series/overview — the DB + slow
filesystem logic we could never test on the host. Covers the status classifier
and the single-scan local counting (the perf fix)."""
from datetime import date, timedelta

from web.models import DownloadJob, MetronIssueCache, Series

PAST = (date.today() - timedelta(days=30)).isoformat()
FUTURE = (date.today() + timedelta(days=30)).isoformat()


def _series(db, **kw):
    defaults = dict(publisher="Image", series_name="Test", year=2020, enabled=True)
    defaults.update(kw)
    s = Series(**defaults)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _cache_issue(db, metron_id, series_metron, number, store_date):
    db.add(MetronIssueCache(metron_id=metron_id, series_id=series_metron,
                            number=number, store_date=store_date))
    db.commit()


def _status_for(client, series_id):
    body = client.get("/api/series/overview").json()
    return next(s["status"] for s in body["series"] if s["id"] == series_id)


def test_local_count_counts_only_comics(db, client, comic_file):
    s = _series(db, series_name="Counter", metron_series_id=10)
    comic_file(s, "Counter #001 (2020).cbz")
    comic_file(s, "Counter #002 (2020).cbr")
    comic_file(s, "cover.jpg")          # ignored
    comic_file(s, "notes.txt")          # ignored
    body = client.get("/api/series/overview").json()
    card = next(c for c in body["series"] if c["id"] == s.id)
    assert card["local_count"] == 2


def test_continuing_complete_when_nothing_past_is_missing(db, client, comic_file):
    s = _series(db, series_name="Ongoing", status="Ongoing", metron_series_id=100)
    _cache_issue(db, 1, 100, "1", PAST)
    _cache_issue(db, 2, 100, "2", PAST)
    comic_file(s, "Ongoing #001 (2020).cbz")
    comic_file(s, "Ongoing #002 (2020).cbz")
    assert _status_for(client, s.id) == "continuing-complete"


def test_ended_complete(db, client, comic_file):
    s = _series(db, series_name="Done", status="Completed", metron_series_id=200)
    _cache_issue(db, 3, 200, "1", PAST)
    comic_file(s, "Done #001 (2020).cbz")
    assert _status_for(client, s.id) == "ended-complete"


def test_missing_past_monitored(db, client, comic_file):
    s = _series(db, series_name="Behind", status="Ongoing", enabled=True, metron_series_id=300)
    _cache_issue(db, 4, 300, "1", PAST)
    _cache_issue(db, 5, 300, "2", PAST)   # past + not on disk -> behind
    comic_file(s, "Behind #001 (2020).cbz")
    assert _status_for(client, s.id) == "missing-monitored"


def test_missing_past_unmonitored(db, client, comic_file):
    s = _series(db, series_name="Paused", status="Ongoing", enabled=False, metron_series_id=400)
    _cache_issue(db, 6, 400, "1", PAST)
    _cache_issue(db, 7, 400, "2", PAST)
    comic_file(s, "Paused #001 (2020).cbz")
    assert _status_for(client, s.id) == "missing-unmonitored"


def test_future_only_gap_is_not_missing(db, client, comic_file):
    """An issue that simply hasn't shipped yet must NOT read as 'behind'."""
    s = _series(db, series_name="Caught Up", status="Ongoing", metron_series_id=500)
    _cache_issue(db, 8, 500, "1", PAST)
    _cache_issue(db, 9, 500, "2", FUTURE)   # not out yet
    comic_file(s, "Caught Up #001 (2020).cbz")
    assert _status_for(client, s.id) == "continuing-complete"


def test_active_download_wins(db, client):
    s = _series(db, series_name="Fetching", status="Completed", metron_series_id=600)
    db.add(DownloadJob(series_id=s.id, issue_number="1", search_term="x", status="downloading"))
    db.commit()
    assert _status_for(client, s.id) == "downloading"


def test_stats_totals(db, client, comic_file):
    a = _series(db, series_name="A", status="Ongoing", total_issues=5, metron_series_id=700)
    _series(db, series_name="B", status="Completed", total_issues=3, enabled=False, metron_series_id=701)
    comic_file(a, "A #001 (2020).cbz")
    stats = client.get("/api/series/overview").json()["stats"]
    assert stats["series"] == 2
    assert stats["ended"] == 1
    assert stats["continuing"] == 1
    assert stats["monitored"] == 1
    assert stats["unmonitored"] == 1
    assert stats["issues_total"] == 8
    assert stats["files_total"] == 1
