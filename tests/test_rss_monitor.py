"""§5 RSS monitoring — the auto-enqueue poll + the worker's direct-URL path.
Feed and all network are mocked; temp DB via conftest."""
import web.worker as worker
from comic_search.rss_feed import FeedEntry
from comic_search.rss_monitor import poll_feed_and_enqueue
from web.models import DownloadJob, MonitoredIssue, Series


def _fe(name, num, year=2026, url=None):
    return FeedEntry(
        title=f"{name} #{num} ({year})",
        url=url or f"https://getcomics.org/comic/{name}-{num}",
        pub_date=None, categories=[], description="",
        series_name=name, issue_number=str(num), year=year,
    )


def _feed(monkeypatch, entries):
    monkeypatch.setattr("comic_search.rss_feed.fetch_feed", lambda *a, **k: entries)


def _spy_enqueue(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "enqueue", lambda jid: calls.append(jid))
    return calls


def _mk(db, **kw):
    kw.setdefault("enabled", True)
    s = Series(publisher="Image", series_name="Spawn", year=1992, **kw)
    db.add(s)
    db.commit()
    return s


def test_poll_enqueues_new_match(db, monkeypatch):
    s = _mk(db, metron_series_id=1)
    _feed(monkeypatch, [_fe("Spawn", 350)])
    calls = _spy_enqueue(monkeypatch)

    res = poll_feed_and_enqueue()

    assert res["enqueued"] == 1
    job = db.query(DownloadJob).filter_by(series_id=s.id).one()
    assert job.issue_number == "350"
    assert job.url == "https://getcomics.org/comic/Spawn-350"
    assert job.source == "rss"
    assert job.status == "queued"
    assert calls == [job.id]


def test_poll_skips_when_local_file_exists(db, monkeypatch, comic_file):
    s = _mk(db)
    comic_file(s, "Spawn #350 (2026).cbz")
    _feed(monkeypatch, [_fe("Spawn", 350)])
    _spy_enqueue(monkeypatch)

    res = poll_feed_and_enqueue()

    assert res["enqueued"] == 0
    assert db.query(DownloadJob).count() == 0


def test_poll_skips_when_any_job_exists(db, monkeypatch):
    # A *failed* job must NOT be retried every poll (no retry-storm).
    s = _mk(db)
    db.add(DownloadJob(series_id=s.id, issue_number="350", search_term="x", status="failed"))
    db.commit()
    _feed(monkeypatch, [_fe("Spawn", 350)])
    calls = _spy_enqueue(monkeypatch)

    res = poll_feed_and_enqueue()

    assert res["enqueued"] == 0
    assert calls == []
    assert db.query(DownloadJob).count() == 1  # no new job


def test_poll_respects_selective_monitoring(db, monkeypatch):
    s = _mk(db)
    db.add(MonitoredIssue(series_id=s.id, issue_number="1", issue_type="regular"))
    db.commit()
    _feed(monkeypatch, [_fe("Spawn", 350)])  # 350 not in monitored {1}
    _spy_enqueue(monkeypatch)

    assert poll_feed_and_enqueue()["enqueued"] == 0


def test_poll_respects_issue_min(db, monkeypatch):
    s = _mk(db, issue_min=400)
    _feed(monkeypatch, [_fe("Spawn", 350)])
    _spy_enqueue(monkeypatch)

    assert poll_feed_and_enqueue()["enqueued"] == 0


def test_poll_skips_disabled_series(db, monkeypatch):
    _mk(db, enabled=False)
    _feed(monkeypatch, [_fe("Spawn", 350)])
    _spy_enqueue(monkeypatch)

    assert poll_feed_and_enqueue()["enqueued"] == 0


def test_poll_skips_non_getcomics_url(db, monkeypatch):
    # Defence in depth: never enqueue a non-getcomics URL for server-side fetch.
    _mk(db)
    _feed(monkeypatch, [_fe("Spawn", 350, url="https://evil.example/x")])
    _spy_enqueue(monkeypatch)

    assert poll_feed_and_enqueue()["enqueued"] == 0


def test_download_endpoint_rejects_foreign_url(client, db):
    s = _mk(db)
    r = client.post(f"/api/series/{s.id}/issues/350/download?url=https://evil.example/x")
    assert r.status_code == 400
    assert db.query(DownloadJob).count() == 0


def test_worker_post_url_skips_search(monkeypatch):
    called = {"search": False}

    def _no_search(*a, **k):
        called["search"] = True
        raise AssertionError("search must not run when post_url is set")

    monkeypatch.setattr(worker, "_search_for_issue", _no_search)
    monkeypatch.setattr("downloader.get_comic_download_url.get_comic_download_url", lambda u: "http://dl/x")
    monkeypatch.setattr("downloader.download_file.download_file", lambda *a, **k: "/tmp/staging/Spawn #350.cbz")
    monkeypatch.setattr("downloader.process_downloaded_comic.process_downloaded_comic", lambda *a, **k: "/tmp/staging/Spawn #350.cbz")
    monkeypatch.setattr("util.staging_dir", lambda: "/tmp/staging")
    monkeypatch.setattr("util.install_to_library", lambda staged, dest: "/tmp/Spawn #350.cbz")
    monkeypatch.setattr("util.create_series_directory", lambda e: "/tmp")

    class _S:
        def to_scraper_tuple(self):
            return ("Image", "Spawn", "1992", None, None, "1", None, 1, None)

    out = worker._download_issue(_S(), "350", post_url="http://gc/post")

    assert called["search"] is False
    assert out == "Spawn #350.cbz"
