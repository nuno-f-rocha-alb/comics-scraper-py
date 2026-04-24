import requests
import logging
from config import API_KEY, BASE_URL, HEADERS_APP
from metadata.get_volume_info import get_volume_info, get_volume_info_by_id
from metadata.get_comic_metadata_metron import get_comic_metadata_metron


def _get_metadata_comicvine(entry, issue_number) -> dict | None:
    """Fetch metadata from ComicVine and return in normalised format."""
    if not entry[3]:
        result = get_volume_info(entry[1], entry[2])
        if not result:
            return None
        volume_id, publisher, issue_count = result
    else:
        volume_id = entry[3]
        result = get_volume_info_by_id(volume_id)
        if not result:
            return None
        publisher, issue_count = result

    issues_url = f"{BASE_URL}/issues/?filter=volume:{volume_id},issue_number:{issue_number}&"
    params = {"api_key": API_KEY, "format": "json", "limit": 1}
    response = requests.get(issues_url, headers=HEADERS_APP, params=params, timeout=15)

    if response.status_code == 403:
        logging.error("ComicVine: access denied. Check your API key.")
        return None

    issue_data = response.json()
    if not (issue_data.get("results") and issue_data["results"]):
        logging.warning(f"ComicVine: no issue found for {entry[1]} #{issue_number}")
        return None

    issue = issue_data["results"][0]
    logging.info(f"ComicVine: found metadata for {entry[1]} #{issue_number}")
    return {
        "series_name": issue.get("volume", {}).get("name", ""),
        "issue_number": issue.get("issue_number", ""),
        "title": issue.get("name", ""),
        "publisher": publisher,
        "description": issue.get("description", ""),
        "issue_count": issue_count,
        "store_date": issue.get("store_date", ""),
    }


def get_comic_metadata(entry, issue_number) -> dict | None:
    """Retrieve metadata for a specific issue. Tries Metron first, falls back to ComicVine."""
    try:
        metadata = get_comic_metadata_metron(entry, issue_number)
        if metadata:
            return metadata
    except Exception as e:
        logging.warning(f"Metron lookup failed for {entry[1]} #{issue_number}: {e}")

    logging.info(f"Falling back to ComicVine for {entry[1]} #{issue_number}")
    return _get_metadata_comicvine(entry, issue_number)
