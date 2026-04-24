import logging
from config import METRON_BASE_URL
from metadata import metron_client

_series_cache: dict = {}


def _series_detail(series_id: int) -> dict:
    if series_id in _series_cache:
        return _series_cache[series_id]
    r = metron_client.get(f"{METRON_BASE_URL}/series/{series_id}/")
    data = r.json()
    result = {
        "publisher": data.get("publisher", {}).get("name", ""),
        "issue_count": data.get("issue_count", 0),
    }
    _series_cache[series_id] = result
    return result


def _find_series_id(cv_id, series_name: str, year_began, metron_series_id=None) -> int | None:
    # Direct Metron ID — no lookup needed (set by web UI when adding a series)
    if metron_series_id:
        return int(metron_series_id)
    # Try by CV ID next — avoids ambiguous name searches
    if cv_id:
        r = metron_client.get(f"{METRON_BASE_URL}/series/", cv_id=int(cv_id))
        results = r.json().get("results", [])
        if results:
            return results[0]["id"]
    # Fall back to name + year search
    r = metron_client.get(f"{METRON_BASE_URL}/series/", name=series_name, year_began=int(year_began))
    results = r.json().get("results", [])
    if results:
        return results[0]["id"]
    return None


def get_comic_metadata_metron(entry, issue_number: str) -> dict | None:
    """Fetch normalised metadata for a single issue from Metron.

    Returns a dict with keys: series_name, issue_number, title, publisher,
    description, issue_count, store_date — or None if not found.
    """
    cv_id = entry[3]
    series_name = entry[1]
    year = entry[2]
    metron_series_id = entry[5] if len(entry) > 5 else None

    series_id = _find_series_id(cv_id, series_name, year, metron_series_id)
    if not series_id:
        logging.warning(f"Metron: series not found for {series_name} ({year})")
        return None

    r = metron_client.get(f"{METRON_BASE_URL}/issue/", series_id=series_id, number=issue_number)
    results = r.json().get("results", [])
    if not results:
        logging.warning(f"Metron: issue #{issue_number} not found in {series_name}")
        return None

    issue_id = results[0]["id"]
    r = metron_client.get(f"{METRON_BASE_URL}/issue/{issue_id}/")
    issue = r.json()
    series_info = _series_detail(series_id)

    logging.info(f"Metron: found metadata for {series_name} #{issue_number}")
    return {
        "series_name": issue["series"]["name"],
        "issue_number": issue["number"],
        "title": issue.get("name", ""),
        "publisher": series_info["publisher"],
        "description": issue.get("desc", ""),
        "issue_count": series_info["issue_count"],
        "store_date": issue.get("store_date", "") or issue.get("cover_date", ""),
    }
