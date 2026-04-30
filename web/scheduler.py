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

def _auto_cleanup_logs() -> None:
    """Delete log files older than log_retention_days (from AppSetting, default 7)."""
    try:
        import os as _os
        import time as _time
        from web.database import SessionLocal
        from web.models import AppSetting

        log_dir = _os.getenv("LOG_DIR", "logs")
        if not _os.path.isdir(log_dir):
            return

        with SessionLocal() as db:
            row = db.get(AppSetting, "log_retention_days")
            retention_days = int(row.value) if row else 7

        cutoff = _time.time() - retention_days * 86400
        # Identify active log file to protect it
        import logging as _logging
        active = None
        for h in _logging.getLogger().handlers:
            if isinstance(h, _logging.FileHandler):
                active = h.baseFilename
                break

        deleted = 0
        for name in _os.listdir(log_dir):
            if not name.endswith(".log"):
                continue
            path = _os.path.join(log_dir, name)
            if active and _os.path.normpath(path) == _os.path.normpath(active):
                continue
            if _os.stat(path).st_mtime < cutoff:
                _os.remove(path)
                deleted += 1

        if deleted:
            log.info("Auto-cleanup: removed %d log file(s) older than %d days.", deleted, retention_days)
    except Exception as exc:
        log.warning("Log auto-cleanup failed: %s", exc)


def start_scheduler() -> None:
    from web.database import init_db
    init_db()
    _auto_cleanup_logs()

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
