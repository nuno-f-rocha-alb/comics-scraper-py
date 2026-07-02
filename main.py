import logging
import time

from comic_search.search_comics import search_comics
from config import *
from downloader.check_and_download_comics import check_and_download_comics
from util import create_series_directory


def _is_http_403(exc: Exception) -> bool:
    """True if exc is (or wraps) an HTTP 403. Duck-typed on the requests
    HTTPError's .response.status_code so no requests import is needed and a
    wrapped/re-raised error still matches."""
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) == 403


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


def run_scraper():
    start_time = time.time()
    # Metron cache (incl. total_issues = issue_max bound) is refreshed by the
    # nightly metron_nightly job for non-ended series; fresh releases come via the
    # RSS poll. So the scraper no longer pre-refreshes Metron every run.
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
            if _is_http_403(exc):
                # Cloudflare human-gate on the mirror (e.g. comicfiles.ru). Expected
                # and unactionable — skip quietly, the next run retries. No traceback.
                logging.warning(
                    "Series %s (%s): mirror blocked (HTTP 403, likely Cloudflare) — "
                    "skipping, will retry next run.",
                    entry[1], entry[0],
                )
            else:
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
