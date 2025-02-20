import requests

from config import *
from metadata.get_volume_info import get_volume_info, get_volume_info_by_id


def get_comic_metadata(entry, issue_number):
    """Retrieve metadata for a specific issue by volume ID and issue number."""
    if not entry[3]:
        volume_id, publisher, issue_count = get_volume_info(entry[1], entry[2])
        if not volume_id:
            return None
    else:
        volume_id=entry[3]
        publisher, issue_count = get_volume_info_by_id(volume_id)


    # Search for the specific issue within the volume
    issues_url = f"{BASE_URL}/issues/?filter=volume:{volume_id},issue_number:{issue_number}&"
    params = {
        "api_key": API_KEY,
        "format": "json",
        "limit": 1
    }
    response = requests.get(issues_url, headers=HEADERS_APP, params=params)

    if response.status_code == 403:
        logging.error("Access denied. Check your API key and User-Agent.")
        return None

    issue_data = response.json()

    # Ensure an issue was found
    if "results" in issue_data and issue_data["results"]:
        issue = issue_data["results"][0]
        issue['publisher'] = publisher
        issue['issue_count'] = issue_count
        logging.info(f"Found metadata for {entry[1]} #{issue_number}.")
        return issue
    else:
        logging.warning(f"No issue found for {entry[1]} #{issue_number}.")
        return None
