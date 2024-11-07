import re
import time
import requests
from bs4 import BeautifulSoup

from config import *
from util import extract_year_from_comic_title


def search_comics(series_name, target_year):
    """Iterates through pages of search results for a series and returns a list of available comics ordered by issue."""
    page = 1
    comics = []

    while True:
        search_url = f"{BASE_SEARCH_URL.format(page)}{series_name.replace(' ', '+')}"
        response = requests.get(search_url, headers=HEADERS)

        if response.status_code == 404 or "No Results Found" in response.text:
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        page_comics = soup.select("div.post-info h1.post-title a")
        if not page_comics:
            break

        for link in page_comics:
            comic_title = link.get_text(strip=True)
            comic_url = link['href']

            # Extract issue number from title
            issue_match = re.search(r"#(\d+)", comic_title)
            issue_number = int(issue_match.group(1)) if issue_match else float(
                'inf')  # Use infinity if no issue number found

            year = extract_year_from_comic_title(comic_title)

            if year is None or year < target_year:
                continue # Skip comics with date ranges or those with a year less than the target year

            comics.append((comic_title, comic_url, issue_number, year))

        logging.info(f"Page {page} searched, {len(page_comics)} comics found.")
        page += 1
        time.sleep(1)

    # Sort comics by issue number
    comics.sort(key=lambda x: (x[3], x[2]))  # Sort by the third element in the tuple (issue_number)

    return [(title, url) for title, url, _, _ in comics]  # Return only title and url