import requests

from config import *
from metadata.get_volume_info import get_volume_info


def get_comic_metadata(series_name, issue_number, starting_year):
    """Retrieve metadata for a specific issue by volume ID and issue number."""
    volume_id, publisher, issue_count = get_volume_info(series_name, starting_year)
    if not volume_id:
        return None

    # Search for the specific issue within the volume
    issues_url = f"{BASE_URL}/issues/"
    params = {
        "api_key": API_KEY,
        "format": "json",
        "filter": f"volume:{volume_id},issue_number:{issue_number}",
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
        logging.info(f"Found metadata for {series_name} #{issue_number}.")
        return issue
    else:
        logging.warning(f"No issue found for {series_name} #{issue_number}.")
        return None
