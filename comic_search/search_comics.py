import re
import time
import requests
from bs4 import BeautifulSoup

from config import *
from util import extract_year_from_comic_title
from comic_search.search_cache import load_cache, save_cache


def search_comics(entry):
    """Iterates through pages of search results for a series and returns a list of available comics ordered by issue.

    Uses a cache to stop early once a page is fully known — since getcomics.org
    returns results newest-first, a fully-cached page means all further pages
    are already known too.
    """
    cached_comics = load_cache(entry[1])
    cached_urls = {url for _, url in cached_comics}

    page = 1
    new_comics = []  # (title, url, issue_number, year) — only entries not in cache

    while True:
        search_url = f"{BASE_SEARCH_URL.format(page)}{entry[1].replace(' ', '+')}"
        response = requests.get(search_url, headers=HEADERS, timeout=15)

        if response.status_code == 404 or "No Results Found" in response.text:
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        page_comics = soup.select("div.post-info h1.post-title a")
        if not page_comics:
            break

        page_urls = {link['href'] for link in page_comics}

        # If every URL on this page is already cached, stop — all further pages are also cached
        if page_urls and page_urls.issubset(cached_urls):
            logging.info(f"Page {page} fully cached, stopping early.")
            break

        for link in page_comics:
            comic_title = link.get_text(strip=True)
            comic_url = link['href']

            if comic_url in cached_urls:
                continue  # already known, skip

            issue_match = re.search(r"#(\d+)", comic_title)
            issue_number = int(issue_match.group(1)) if issue_match else float('inf')

            year = extract_year_from_comic_title(comic_title)
            if year is None or year < entry[2]:
                continue

            new_comics.append((comic_title, comic_url, issue_number, year))

        logging.info(f"Page {page} searched, {len(page_comics)} comics found.")
        page += 1
        time.sleep(1)

    # Merge cache + new, update cache
    all_title_url = cached_comics + [(t, u) for t, u, _, _ in new_comics]
    save_cache(entry[1], all_title_url)

    # Build the full sorted result list (cached entries need their issue/year re-parsed for sorting)
    def parse_sort_key(title):
        issue_match = re.search(r"#(\d+)", title)
        issue_number = int(issue_match.group(1)) if issue_match else float('inf')
        year = extract_year_from_comic_title(title) or 0
        return (year, issue_number)

    all_comics = all_title_url
    all_comics.sort(key=lambda x: parse_sort_key(x[0]))

    return all_comics
