"""Feed-driven monitoring — the main download path (§5).

Polls the getcomics.org RSS feed and auto-enqueues downloads for new issues of
monitored series. One feed fetch per tick (~10 newest posts), matched in-memory
against the DB. The per-series search scraper stays as the back-catalog net.

Dedup needs no extra table: an issue is skipped when a local file exists OR any
DownloadJob already exists for (series, issue) in any status — so a *failed*
download is not retried every tick (no retry-storm).
"""
import logging

log = logging.getLogger(__name__)


def _norm(n: str) -> str:
    try:
        return str(int(float(n)))
    except (ValueError, TypeError):
        return n or ""


def _has_existing_job(db, series_id: int, num: str) -> bool:
    """True if any DownloadJob (any status) already covers (series_id, num)."""
    from web.models import DownloadJob
    target = _norm(num)
    jobs = db.query(DownloadJob).filter(DownloadJob.series_id == series_id).all()
    return any(_norm(j.issue_number) == target for j in jobs)


def _issue_is_monitored(db, series, num: str) -> bool:
    """Auto-download safety gate — same rules the scraper honours.

    Selective monitoring (regular MonitoredIssue rows present) → only the listed
    issues; none present → monitor-all. Plus the series issue_min lower bound.
    """
    from web.models import MonitoredIssue

    rows = (
        db.query(MonitoredIssue)
        .filter(MonitoredIssue.series_id == series.id, MonitoredIssue.issue_type == "regular")
        .all()
    )
    if rows and _norm(num) not in {_norm(r.issue_number) for r in rows}:
        return False

    if series.issue_min:
        try:
            if int(float(num)) < series.issue_min:
                return False
        except (ValueError, TypeError):
            pass
    return True


def poll_feed_and_enqueue() -> dict:
    """Fetch the feed, enqueue new monitored matches. Returns counts.

    Exceptions from the feed fetch propagate to the scheduler wrapper (logged).
    """
    from comic_search.rss_feed import fetch_feed
    from web.app import _match_feed_entries
    from web.database import SessionLocal
    from web.models import DownloadJob
    from web import worker
    from util import is_getcomics_url

    entries = fetch_feed()
    enqueued = skipped = 0

    with SessionLocal() as db:
        for m in _match_feed_entries(entries, db):
            s, num, entry = m["series"], m["issue_number"], m["entry"]
            if (
                m["downloaded"]
                or not is_getcomics_url(entry.url)
                or _has_existing_job(db, s.id, num)
                or not _issue_is_monitored(db, s, num)
            ):
                skipped += 1
                continue
            job = DownloadJob(
                series_id=s.id,
                issue_number=num,
                search_term=entry.title,
                url=entry.url,
                source="rss",
                status="queued",
            )
            db.add(job)
            db.commit()
            worker.enqueue(job.id)
            enqueued += 1
            log.info("RSS: queued %s #%s (%s)", s.series_name, num, entry.url)

    if enqueued:
        log.info("RSS poll: %d feed entries, %d queued, %d skipped", len(entries), enqueued, skipped)
    return {"feed_size": len(entries), "enqueued": enqueued, "skipped": skipped}
