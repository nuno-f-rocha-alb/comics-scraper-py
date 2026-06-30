"""Phase-B reading-list suggestions — a bounded, manual background scan.

Metron has no reverse "lists containing issue X" lookup, so we scan public,
highly-rated lists for the publishers the user collects and compute coverage
against owned issues. Mirrors web/metron_refresh.py (daemon thread + polled
status). Manual trigger only.
"""
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)

COVERAGE_FLOOR = 0.25  # don't cache lists below this; display filters by the threshold setting

_lock = threading.Lock()
_running = False
_last_run_at: datetime | None = None
_last_error: str | None = None
_progress: dict = {"current": "", "done": 0, "total": 0}
_last_result: dict = {"scanned": 0, "kept": 0}


def _norm(n) -> str:
    """'001' == '1', but keep '1.5' distinct (decimal issues must not collide)."""
    raw = str(n or "").strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return raw
    return str(int(value)) if value == value.to_integral_value() else format(value.normalize(), "f")


def compute_coverage(items: list[dict], owned_map: dict[tuple, set]) -> tuple[int, int]:
    """owned = items whose series (matched by name+year — Metron's reading-list
    payload has no series id) is tracked and whose number is owned locally.
    owned_map: {(normalized series_name, year): {local issue numbers}}."""
    total = len(items)
    owned = 0
    for it in items:
        key = ((it.get("series_name") or "").strip().lower(), it.get("series_year"))
        nums = owned_map.get(key)
        if nums and _norm(it.get("number")) in nums:
            owned += 1
    return owned, total


def get_status() -> dict:
    with _lock:
        return {
            "running": _running,
            "last_run_at": _last_run_at,
            "last_error": _last_error,
            "progress": dict(_progress),
            "last_result": dict(_last_result),
        }


def run_scan() -> bool:
    """Spawn the suggestion scan. Returns False if already running."""
    global _running
    with _lock:
        if _running:
            return False
        _running = True

    def _worker():
        global _running, _last_run_at, _last_error, _progress, _last_result
        with _lock:
            _last_error = None
            _progress = {"current": "", "done": 0, "total": 0}

        from web.app import _local_issue_numbers, _get_setting
        from web.database import SessionLocal
        from web.models import ReadingList, Series, SuggestedReadingList
        from metadata.metron_client import RateLimitedError
        from metadata import metron_reading_lists as rl

        db = SessionLocal()
        scanned = kept = 0
        try:
            min_rating = _get_setting(db, "rl_suggest_min_rating", "3")
            max_lists = int(_get_setting(db, "rl_suggest_max", "200"))

            owned_map: dict[tuple, set] = {}
            publishers: set[str] = set()
            for s in db.query(Series).all():
                owned_map[((s.series_name or "").strip().lower(), s.year)] = _local_issue_numbers(s)
                if s.publisher:
                    publishers.add(s.publisher)
            already_added = {r.metron_id for r in db.query(ReadingList.metron_id).all()}

            # Gather candidate lists per publisher (one page each), bounded.
            candidates: dict[int, dict] = {}
            for pub in sorted(publishers):
                for c in rl.search_reading_lists(block=True, publisher=pub, average_rating__gte=min_rating):
                    cid = c.get("id")
                    if cid and cid not in already_added and cid not in candidates:
                        candidates[cid] = c
                if len(candidates) >= max_lists:
                    break
            cand_list = list(candidates.values())[:max_lists]

            with _lock:
                _progress = {"current": "", "done": 0, "total": len(cand_list)}

            completed = True
            kept_ids: set[int] = set()
            for i, c in enumerate(cand_list):
                with _lock:
                    _progress = {"current": c.get("name", ""), "done": i, "total": len(cand_list)}
                try:
                    items = [rl.parse_item(x) for x in rl.get_reading_list_items(c["id"], block=True)]
                except RateLimitedError as exc:
                    completed = False
                    with _lock:
                        _last_error = str(exc)
                    log.warning("Suggestion scan rate-limited — stopping: %s", exc)
                    break
                scanned += 1
                owned, total = compute_coverage(items, owned_map)
                coverage = owned / total if total else 0.0
                if coverage < COVERAGE_FLOOR:
                    continue
                row = db.get(SuggestedReadingList, c["id"]) or SuggestedReadingList(metron_id=c["id"])
                row.name = c.get("name") or f"List {c['id']}"
                row.image_url = c.get("image")
                row.list_type = c.get("list_type")
                row.attribution_source = c.get("attribution_source")
                row.average_rating = c.get("average_rating")
                row.owned, row.total, row.coverage = owned, total, coverage
                row.computed_at = datetime.now(timezone.utc)
                db.add(row)
                kept_ids.add(c["id"])
                kept += 1

            # Drop stale cached suggestions — but only on a complete run, so a
            # rate-limited partial scan doesn't wipe valid cached entries.
            if completed:
                for row in db.query(SuggestedReadingList).all():
                    if row.metron_id not in kept_ids:
                        db.delete(row)
            db.commit()
        except Exception as exc:
            db.rollback()
            with _lock:
                _last_error = str(exc)
            log.exception("Suggestion scan aborted")
        finally:
            db.close()
            with _lock:
                _running = False
                _last_run_at = datetime.now(timezone.utc)
                _last_result = {"scanned": scanned, "kept": kept}
                _progress = {"current": "", "done": _progress["total"], "total": _progress["total"]}
            log.info("Suggestion scan finished — %d scanned, %d kept", scanned, kept)

    threading.Thread(target=_worker, daemon=True, name="rl-suggest").start()
    return True
