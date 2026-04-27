import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

SCHEDULE_INTERVAL_HOURS = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "24"))

_scheduler = BackgroundScheduler()


def start_scheduler():
    from main import run_scraper

    _scheduler.add_job(
        run_scraper,
        trigger="interval",
        hours=SCHEDULE_INTERVAL_HOURS,
        id="scraper",
        next_run_time=datetime.now(),  # run immediately on startup
    )
    _scheduler.start()
    log.info("Scheduler started — scraper runs every %dh.", SCHEDULE_INTERVAL_HOURS)


def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
