"""Background Metron refresh — refreshes tracked-series metadata/covers/issue
lists off the request thread. Mirrors web/scanner.py (daemon thread + polled
status). Replaces the old synchronous /api/sync-covers + /api/metron/cache/refresh
which could block an HTTP request for minutes on a Metron rate-limit sleep."""
import logging
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_running = False
_last_refresh_at: datetime | None = None
_last_error: str | None = None
_progress: dict = {"current": "", "done": 0, "total": 0}
_last_result: dict = {"refreshed": 0, "ids_found": 0, "skipped": 0, "errors": 0}

log = logging.getLogger(__name__)


def get_status() -> dict:
    with _lock:
        return {
            "running": _running,
            "last_refresh_at": _last_refresh_at,
            "last_error": _last_error,
            "progress": dict(_progress),
            "last_result": dict(_last_result),
        }


def run_refresh(force: bool = True, skip_titles: bool = True, only_active: bool = False) -> bool:
    """Spawn a background Metron refresh thread. Returns False if already running.

    skip_titles=False also resolves per-issue titles (heavier; for the nightly
    job which blocks through burst limits). only_active=True refreshes only
    non-ended series (ended ones get no new issues)."""
    global _running
    with _lock:
        if _running:
            return False
        _running = True

    def _worker():
        global _running, _last_refresh_at, _last_error, _progress, _last_result

        counts = {"refreshed": 0, "ids_found": 0, "skipped": 0, "errors": 0}
        with _lock:
            _last_error = None
            _progress = {"current": "", "done": 0, "total": 0}

        db = None
        try:
            # Lazy import inside the try so an import/connect failure still hits
            # the finally that clears _running (else refresh stays blocked).
            # web.app imports this module, so a top-level import would be
            # circular (same trick scanner.py uses for retag_comics).
            from web.app import _refresh_one_series, _is_series_ended
            from web.database import SessionLocal
            from web.models import Series
            from metadata.metron_client import RateLimitedError

            db = SessionLocal()
            rows = db.query(Series).order_by(Series.publisher, Series.series_name).all()
            if only_active:
                rows = [s for s in rows if not _is_series_ended(s)]
            total = len(rows)
            with _lock:
                _progress = {"current": "", "done": 0, "total": total}
            log.info("Metron refresh started — %d series, force=%s", total, force)

            for i, s in enumerate(rows):
                with _lock:
                    _progress = {"current": s.series_name, "done": i, "total": total}
                try:
                    before_id = s.metron_series_id
                    refreshed = _refresh_one_series(s, db, force=force, skip_titles=skip_titles)
                    # Commit per series so a later failure's rollback can't
                    # discard the series already refreshed in this run.
                    db.commit()
                    if s.metron_series_id and not before_id:
                        counts["ids_found"] += 1
                    counts["refreshed" if refreshed else "skipped"] += 1
                except RateLimitedError as exc:
                    db.rollback()
                    with _lock:
                        _last_error = str(exc)
                    log.warning("Metron rate limited — stopping refresh: %s", exc)
                    break
                except Exception as exc:
                    db.rollback()
                    counts["errors"] += 1
                    log.warning("Could not refresh Metron meta for %s: %s", s.series_name, exc)
        except Exception as exc:
            with _lock:
                _last_error = str(exc)
            log.exception("Metron refresh aborted with unexpected error")
        finally:
            if db is not None:
                db.close()
            with _lock:
                _running = False
                _last_refresh_at = datetime.now(timezone.utc)
                _last_result = counts
                _progress = {"current": "", "done": _progress["total"], "total": _progress["total"]}
            log.info(
                "Metron refresh finished — %d refreshed, %d ids found, %d skipped, %d errors",
                counts["refreshed"], counts["ids_found"], counts["skipped"], counts["errors"],
            )

    try:
        threading.Thread(target=_worker, daemon=True, name="metron-refresh").start()
    except Exception:
        # Don't leave _running stuck on if the thread never starts.
        with _lock:
            _running = False
        raise
    return True
