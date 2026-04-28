"""APScheduler wrapper with DB-persisted config and manual trigger support."""
import logging
import os
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

_DEFAULT_HOURS = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "24"))
_JOB_ID = "scraper"

_scheduler = BackgroundScheduler()
_running = False
_last_run_at: datetime | None = None
_last_run_error: str | None = None
_lock = threading.Lock()


# ── Config persistence ────────────────────────────────────────────────────────

def _get_setting(db, key: str, default: str) -> str:
    from web.models import AppSetting
    row = db.get(AppSetting, key)
    return row.value if row else default


def _set_setting(db, key: str, value: str) -> None:
    from web.models import AppSetting
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def load_config() -> tuple[str, str]:
    """Return (mode, value). mode: 'interval' | 'cron'."""
    try:
        from web.database import SessionLocal
        with SessionLocal() as db:
            mode = _get_setting(db, "scheduler_mode", "interval")
            value = _get_setting(db, "scheduler_value", str(_DEFAULT_HOURS))
            return mode, value
    except Exception:
        return "interval", str(_DEFAULT_HOURS)


def save_config(mode: str, value: str) -> None:
    from web.database import SessionLocal
    with SessionLocal() as db:
        _set_setting(db, "scheduler_mode", mode)
        _set_setting(db, "scheduler_value", value)
        db.commit()


def make_trigger(mode: str, value: str):
    if mode == "cron":
        return CronTrigger.from_crontab(value)
    return IntervalTrigger(hours=max(1, int(value)))


# ── Run logic ─────────────────────────────────────────────────────────────────

def _wrapped_run() -> None:
    global _running, _last_run_at, _last_run_error
    with _lock:
        if _running:
            log.warning("Scraper already running — skipping trigger.")
            return
        _running = True
        _last_run_error = None

    try:
        from main import run_scraper
        run_scraper()
    except Exception as exc:
        _last_run_error = str(exc)
        log.error("Scraper run failed: %s", exc)
    finally:
        _last_run_at = datetime.now(timezone.utc)
        with _lock:
            _running = False


def trigger_now() -> None:
    """Run the scraper immediately in a background thread."""
    t = threading.Thread(target=_wrapped_run, daemon=True, name="scraper-manual")
    t.start()


def is_running() -> bool:
    return _running


def get_status() -> dict:
    job = _scheduler.get_job(_JOB_ID)
    mode, value = load_config()
    return {
        "running": _running,
        "last_run_at": _last_run_at,
        "last_run_error": _last_run_error,
        "next_run_at": job.next_run_time if job else None,
        "mode": mode,
        "value": value,
    }


def update_schedule(mode: str, value: str) -> None:
    save_config(mode, value)
    trigger = make_trigger(mode, value)
    _scheduler.reschedule_job(_JOB_ID, trigger=trigger)
    log.info("Schedule updated: mode=%s value=%s", mode, value)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    from web.database import init_db
    init_db()

    mode, value = load_config()
    trigger = make_trigger(mode, value)

    _scheduler.add_job(
        _wrapped_run,
        trigger=trigger,
        id=_JOB_ID,
        next_run_time=datetime.now(),
    )
    _scheduler.start()
    log.info("Scheduler started — mode=%s value=%s", mode, value)


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
