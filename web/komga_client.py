"""Minimal Komga client for pushing a reading list (read-list) via its API.

Config is env-only (config.KOMGA_URL / KOMGA_API_KEY). Matching is best-effort:
Komga is searched by series title, then a book whose metadata.number matches.
Unmatched items are reported rather than failing the whole push.
"""
import logging
from decimal import Decimal, InvalidOperation

import requests

from config import KOMGA_URL, KOMGA_API_KEY

log = logging.getLogger(__name__)
_TIMEOUT = 20


def is_configured() -> bool:
    return bool(KOMGA_URL and KOMGA_API_KEY)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"X-API-Key": KOMGA_API_KEY, "Accept": "application/json"})
    return s


def _norm_num(n) -> str:
    """Normalise an issue number for comparison: '1' == '001' == '1.0', but
    '1.5' stays distinct from '1' (decimal issues must not collide)."""
    text = str(n or "").strip()
    if not text:
        return ""
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return text
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _book_ids_for_series(s: requests.Session, series_name: str) -> dict[str, str]:
    """Return {normalised number: book_id} for the best-matching Komga series."""
    r = s.get(f"{KOMGA_URL}/api/v1/series", params={"search": series_name, "size": 5}, timeout=_TIMEOUT)
    r.raise_for_status()
    matches = r.json().get("content", [])
    if not matches:
        return {}
    # Prefer an exact (case-insensitive) title match, else the first result.
    target = series_name.strip().lower()
    chosen = next((m for m in matches if (m.get("metadata", {}).get("title") or m.get("name", "")).strip().lower() == target), matches[0])
    sid = chosen["id"]
    rb = s.get(f"{KOMGA_URL}/api/v1/series/{sid}/books", params={"size": 2000}, timeout=_TIMEOUT)
    rb.raise_for_status()
    out: dict[str, str] = {}
    for b in rb.json().get("content", []):
        num = b.get("metadata", {}).get("number")
        if num is not None:
            out[_norm_num(num)] = b["id"]
    return out


def push_reading_list(name: str, summary: str, entries: list[tuple[str, str]]) -> dict:
    """entries: ordered (series_name, number) tuples. Creates or updates the
    Komga read list by name. Returns {created, matched, unmatched, readlist_id}."""
    if not is_configured():
        raise RuntimeError("Komga not configured")

    s = _session()
    series_cache: dict[str, dict[str, str]] = {}
    book_ids: list[str] = []
    unmatched: list[str] = []

    for series_name, number in entries:
        if series_name not in series_cache:
            try:
                series_cache[series_name] = _book_ids_for_series(s, series_name)
            except requests.RequestException as exc:
                log.warning("Komga series lookup failed for %s: %s", series_name, exc)
                series_cache[series_name] = {}
        bid = series_cache[series_name].get(_norm_num(number))
        if bid:
            book_ids.append(bid)
        else:
            unmatched.append(f"{series_name} #{number}")

    # Find an existing read list with the same name to update instead of duplicating.
    existing_id = None
    rl = s.get(f"{KOMGA_URL}/api/v1/readlists", params={"search": name, "size": 20}, timeout=_TIMEOUT)
    rl.raise_for_status()
    for r in rl.json().get("content", []):
        if (r.get("name") or "").strip().lower() == name.strip().lower():
            existing_id = r["id"]
            break

    if existing_id:
        resp = s.patch(
            f"{KOMGA_URL}/api/v1/readlists/{existing_id}",
            json={"bookIds": book_ids}, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return {"created": False, "matched": len(book_ids), "unmatched": unmatched, "readlist_id": existing_id}

    resp = s.post(
        f"{KOMGA_URL}/api/v1/readlists",
        json={"name": name, "summary": summary, "ordered": True, "bookIds": book_ids},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    new_id = resp.json().get("id") if resp.content else None
    return {"created": True, "matched": len(book_ids), "unmatched": unmatched, "readlist_id": new_id}
