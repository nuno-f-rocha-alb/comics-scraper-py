"""Metron reading-list API client + item parsing.

Read-only endpoints (Metron doesn't allow create/update via API):
  /reading_list/                 list + filters
  /reading_list/{id}/            detail
  /reading_list/{id}/items/      issues in reading order (paginated)
"""
from config import METRON_BASE_URL
from metadata import metron_client  # call metron_client.get() so tests can patch it

# Filters we forward to Metron (others are ignored).
SEARCH_FILTERS = ("name", "publisher", "list_type", "attribution_source", "average_rating__gte")


def search_reading_lists(block: bool = False, **filters) -> list[dict]:
    """One page (50) of public reading lists matching the given filters."""
    params = {k: v for k, v in filters.items() if k in SEARCH_FILTERS and v not in (None, "")}
    params["is_private"] = "false"  # only ever expose public lists
    r = metron_client.get(f"{METRON_BASE_URL}/reading_list/", block=block, **params)
    return r.json().get("results", [])


def get_reading_list_detail(metron_id: int, block: bool = False) -> dict:
    r = metron_client.get(f"{METRON_BASE_URL}/reading_list/{metron_id}/", block=block)
    return r.json()


def get_reading_list_items(metron_id: int, block: bool = False) -> list[dict]:
    """All items across pages, in reading order."""
    items: list[dict] = []
    url = f"{METRON_BASE_URL}/reading_list/{metron_id}/items/"
    while url:
        data = metron_client.get(url, block=block).json()
        items.extend(data.get("results", []))
        url = data.get("next")
    return items


def _year(date_str) -> int | None:
    """Extract the year from a Metron date like '2015-07-01'."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None


def parse_item(raw: dict) -> dict:
    """Flatten a Metron reading-list item into the fields ReadingListItem stores.

    Note: the /items/ endpoint's nested `series` has NO id — only name, volume
    and year_began — so series are linked by name + year (not a series id)."""
    issue = raw.get("issue") or {}
    series = issue.get("series") or {}
    return {
        "order": raw.get("order") or 0,
        "issue_type": raw.get("issue_type") or "",
        "metron_issue_id": issue.get("id"),
        "series_name": series.get("name"),
        "series_year": series.get("year_began"),
        "series_volume": series.get("volume"),
        "number": issue.get("number"),
        "cover_year": _year(issue.get("cover_date")) or _year(issue.get("store_date")),
        "cv_issue_id": issue.get("cv_id"),
    }
