import logging
import time

from comic_search.search_comics import search_comics
from config import *
from downloader.check_and_download_comics import check_and_download_comics
from util import create_series_directory


def _load_series() -> list[tuple]:
    from web.database import SessionLocal, init_db
    from web.models import MonitoredIssue, Series

    init_db()
    with SessionLocal() as db:
        rows = (
            db.query(Series)
            .filter(Series.enabled == True)  # noqa: E712
            .order_by(Series.publisher, Series.series_name)
            .all()
        )
        result = []
        for r in rows:
            monitored = (
                db.query(MonitoredIssue)
                .filter(MonitoredIssue.series_id == r.id)
                .all()
            )
            if monitored:
                monitored_regular = frozenset(m.issue_number for m in monitored if m.issue_type == "regular")
                monitored_annual  = frozenset(m.issue_number for m in monitored if m.issue_type == "annual")
            else:
                monitored_regular = None
                monitored_annual  = None
            result.append((r.id, r.to_scraper_tuple(), monitored_regular, monitored_annual))

    if not result:
        logging.warning("No series found in DB. Run migrate_series_list.py first.")
    return result


def _refresh_metron_caches() -> None:
    """Force-refresh the Metron issue cache for every enabled series before the
    scraper run, so Series.total_issues (= scraper issue_max upper bound) is
    current. Without this, a series that gains a new issue on Metron would be
    invisible to the scraper until the user manually clicks Refresh in the UI.
    """
    from web.app import _refresh_one_series
    from web.database import SessionLocal
    from web.models import Series
    from metadata.metron_client import RateLimitedError

    with SessionLocal() as db:
        rows = (
            db.query(Series)
            .filter(Series.enabled == True)  # noqa: E712
            .filter(Series.metron_series_id.isnot(None))
            .all()
        )
        if not rows:
            return
        logging.info("Refreshing Metron caches for %d series before scrape…", len(rows))
        for s in rows:
            # force=False → the TTL skips redundant per-series *detail* calls,
            # but _refresh_one_series always force-refreshes the issue list, so
            # newly-released issues stay visible to the scraper every run.
            try:
                _refresh_one_series(s, db, force=False)
            except RateLimitedError:
                logging.warning("Metron rate limited — skipping remaining series.")
                db.commit()
                return
            except Exception as exc:
                logging.warning(
                    "Could not refresh Metron cache for %s: %s", s.series_name, exc
                )
        db.commit()


def run_scraper():
    start_time = time.time()
    _refresh_metron_caches()
    series_list = _load_series()

    for series_id, entry, monitored_regular, monitored_annual in series_list:
        logging.info("Searching for comics in series: %s by %s", entry[1], entry[0])
        try:
            available_comics = search_comics(entry)
            if available_comics:
                local_dir = create_series_directory(entry)
                check_and_download_comics(
                    entry, available_comics, local_dir,
                    series_id=series_id,
                    monitored_regular=monitored_regular,
                    monitored_annual=monitored_annual,
                )
            else:
                logging.warning("No comics found for series: %s", entry[1])
        except Exception as exc:
            logging.error(
                "Series %s (%s) failed; continuing with next series. Error: %s",
                entry[1], entry[0], exc,
                exc_info=True,
            )

    elapsed = time.time() - start_time
    minutes, seconds = int(elapsed // 60), elapsed % 60
    if minutes > 0:
        logging.info("Total execution time: %d minutes and %.2f seconds", minutes, seconds)
    else:
        logging.info("Total execution time: %.2f seconds", seconds)


if __name__ == "__main__":
    run_scraper()
