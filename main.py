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
            monitored_set = (
                frozenset(m.issue_number for m in monitored) if monitored else None
            )
            result.append((r.to_scraper_tuple(), monitored_set))

    if not result:
        logging.warning("No series found in DB. Run migrate_series_list.py first.")
    return result


def run_scraper():
    start_time = time.time()
    series_list = _load_series()

    for entry, monitored_set in series_list:
        logging.info("Searching for comics in series: %s by %s", entry[1], entry[0])
        available_comics = search_comics(entry)
        if available_comics:
            local_dir = create_series_directory(entry)
            check_and_download_comics(entry, available_comics, local_dir, monitored_set=monitored_set)
        else:
            logging.warning("No comics found for series: %s", entry[1])

    elapsed = time.time() - start_time
    minutes, seconds = int(elapsed // 60), elapsed % 60
    if minutes > 0:
        logging.info("Total execution time: %d minutes and %.2f seconds", minutes, seconds)
    else:
        logging.info("Total execution time: %.2f seconds", seconds)


if __name__ == "__main__":
    run_scraper()
