import requests

from config import *

def get_volume_info(series_name, target_year=None):
    """Fetch the volume ID using the series name."""
    search_url = f"{BASE_URL}/search/"
    params = {
        "api_key": API_KEY,
        "format": "json",
        "query": series_name,
        "resources": "volume",
    }
    response = requests.get(search_url, headers=HEADERS_APP, params=params)

    if response.status_code == 403:
        logging.error("Access denied. Check your API key and User-Agent.")
        return None

    data = response.json()

    if "results" in data and data["results"]:
        # Iterate through all volumes to find the one matching the series name
        for volume in data["results"]:
            volume_name = volume.get("name", "").lower()
            if volume_name == series_name.lower():  # Case-insensitive comparison
                # If a target year is provided, check against it
                if target_year is not None:
                    if volume.get("start_year") == target_year:
                        volume_id = volume["id"]
                        # Get the publisher from the volume details
                        publisher = volume.get("publisher", {}).get("name", "")
                        issue_count = volume.get("count_of_issues", 0)
                        logging.info(f"Found volume ID {volume_id} for series {series_name} ({target_year}).")
                        return volume_id, publisher, issue_count

            else:
                # If no year filter is applied, return the first match
                volume_id = volume["id"]
                publisher = volume.get("publisher", {}).get("name", "")
                issue_count = volume.get("count_of_issues", 0)
                logging.info(f"Found volume ID {volume_id} for series {series_name}.")
                return volume_id, publisher, issue_count

        logging.warning(f"No exact match found for series name: {series_name} with year filter: {target_year}")
    else:
        logging.warning(f"No volume found for series name: {series_name}")

    return None

def get_volume_info_by_id(volume_id):
    """Fetch volume details using the volume ID."""
    volume_url = f"{BASE_URL}/volume/4050-{volume_id}/"
    params = {
        "api_key": API_KEY,
        "format": "json",
    }
    response = requests.get(volume_url, headers=HEADERS_APP, params=params)

    if response.status_code == 403:
        logging.error("Access denied. Check your API key and User-Agent.")
        return None

    data = response.json()

    if "results" in data:
        volume = data["results"]
        publisher = volume.get("publisher", {}).get("name", "")
        issue_count = volume.get("count_of_issues", 0)
        logging.info(f"Found volume details for volume ID {volume_id}.")
        return publisher, issue_count
    logging.warning(f"No details found for volume ID {volume_id}.")
    return None