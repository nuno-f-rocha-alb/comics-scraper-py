"""Background library scanner — retagging CBZ files with Metron/ComicVine metadata."""
import logging
import threading
from datetime import datetime

_lock = threading.Lock()
_running = False
_last_scan_at: datetime | None = None
_last_scan_error: str | None = None
_progress: dict = {"current": "", "done": 0, "total": 0}

log = logging.getLogger(__name__)


def get_status() -> dict:
    with _lock:
        return {
            "running": _running,
            "last_scan_at": _last_scan_at,
            "last_scan_error": _last_scan_error,
            "progress": dict(_progress),
        }


def run_scan(series_list: list[tuple], force: bool = False) -> bool:
    """Spawn a background scan thread. Returns False if already running."""
    global _running
    with _lock:
        if _running:
            return False
        _running = True

    def _worker():
        global _running, _last_scan_at, _last_scan_error, _progress

        # Importing retag_comics also imports config.py, which ensures the
        # file handler is attached to the root logger before we start logging.
        from retag_comics import retag_series

        total = len(series_list)
        with _lock:
            _progress = {"current": "", "done": 0, "total": total}
            _last_scan_error = None

        mode = "force retag" if force else "tag missing only"
        log.info("Library scan started — %d series, mode: %s", total, mode)

        tagged_total = skipped_total = error_total = 0

        try:
            for i, entry in enumerate(series_list):
                name = entry[1]
                with _lock:
                    _progress = {"current": name, "done": i, "total": total}
                log.info("[%d/%d] Scanning: %s", i + 1, total, name)
                try:
                    retag_series(entry, force=force)
                except Exception as exc:
                    error_total += 1
                    log.warning("Error retagging %s: %s", name, exc)
        except Exception as exc:
            with _lock:
                _last_scan_error = str(exc)
            log.exception("Library scan aborted with unexpected error")
        finally:
            with _lock:
                _running = False
                _last_scan_at = datetime.utcnow()
                _progress["done"] = _progress["total"]
                _progress["current"] = ""
            log.info(
                "Library scan finished — %d series processed, %d errors",
                total - error_total,
                error_total,
            )

    threading.Thread(target=_worker, daemon=True, name="library-scanner").start()
    return True
