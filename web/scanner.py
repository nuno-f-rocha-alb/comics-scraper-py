"""Background library scanner — retagging CBZ files with Metron/ComicVine metadata."""
import logging
import threading
from datetime import datetime

_lock = threading.Lock()
_running = False
_last_scan_at: datetime | None = None
_last_scan_error: str | None = None
_progress: dict = {"current": "", "done": 0, "total": 0}


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
        from retag_comics import retag_series

        total = len(series_list)
        with _lock:
            _progress = {"current": "", "done": 0, "total": total}
            _last_scan_error = None

        try:
            for i, entry in enumerate(series_list):
                with _lock:
                    _progress = {"current": entry[1], "done": i, "total": total}
                try:
                    retag_series(entry, force=force)
                except Exception as exc:
                    logging.warning(f"Error retagging {entry[1]}: {exc}")
        except Exception as exc:
            with _lock:
                _last_scan_error = str(exc)
            logging.exception("Library scan failed")
        finally:
            with _lock:
                _running = False
                _last_scan_at = datetime.utcnow()
                _progress["done"] = _progress["total"]
                _progress["current"] = ""

    threading.Thread(target=_worker, daemon=True).start()
    return True
