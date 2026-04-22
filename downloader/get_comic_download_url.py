from config import *
import re
import time
import requests
from bs4 import BeautifulSoup

# Mirrors we can convert to a direct download URL
_SUPPORTED_MIRRORS = ["PIXELDRAIN"]
# Mirrors found on pages but requiring external clients — logged and skipped
_UNSUPPORTED_MIRRORS = ["MEGA", "TERABOX", "ROOTZ", "VIKINGFILE"]


def _resolve_url(href):
    """Follow one redirect if present (old site behaviour), otherwise return href directly.

    If the href already ends with a comic file extension it is returned immediately
    without making an extra HTTP request — fetching a 50+ MB file just to check
    for a redirect would time out and return None.
    """
    if re.search(r'\.(cbz|cbr|pdf|zip)([?#]|$)', href, re.IGNORECASE):
        return href
    try:
        r = requests.get(href, headers=HEADERS, allow_redirects=False, timeout=10)
        if r.status_code in (301, 302, 303, 307, 308) and "Location" in r.headers:
            return r.headers["Location"]
        return href
    except Exception as e:
        logging.warning(f"Could not resolve redirect for {href}: {e}")
        return None


def _pixeldrain_direct(url):
    """Convert a Pixeldrain page URL to its direct download API URL."""
    m = re.search(r"pixeldrain\.com/u/([^/?#]+)", url)
    if m:
        return f"https://pixeldrain.com/api/file/{m.group(1)}"
    return url


def get_comic_download_url(comic_url):
    """Retrieves a direct download URL from the comic's page.

    Tries the main 'DOWNLOAD NOW' button first, then falls back to Pixeldrain
    if available. Other mirror links (MEGA, TERABOX, etc.) require external
    clients and are skipped.
    """
    time.sleep(2)
    response = requests.get(comic_url, headers=HEADERS, timeout=15)

    if response.status_code == 429:
        logging.warning(f"Rate limited (429) fetching {comic_url}. Waiting 30s before retrying...")
        time.sleep(30)
        response = requests.get(comic_url, headers=HEADERS, timeout=15)

    if response.status_code != 200:
        logging.warning(f"Unexpected HTTP {response.status_code} for {comic_url}.")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Try the main download button (handles both old redirect and new direct links)
    download_link = soup.find("a", title="DOWNLOAD NOW") or soup.find("a", title="Download Now")
    if download_link:
        url = _resolve_url(download_link["href"])
        if url:
            return url

    # Fall back to supported mirror links
    for mirror_name in _SUPPORTED_MIRRORS:
        mirror_link = soup.find("a", string=mirror_name)
        if mirror_link:
            href = mirror_link["href"]
            if "pixeldrain" in href:
                logging.info(f"Main link failed, falling back to Pixeldrain for {comic_url}.")
                return _pixeldrain_direct(href)

    # Log any unsupported mirrors that were found
    found_unsupported = [
        name for name in _UNSUPPORTED_MIRRORS if soup.find("a", string=name)
    ]
    if found_unsupported:
        logging.warning(
            f"No downloader link found for {comic_url}. "
            f"Mirrors available but require external clients: {', '.join(found_unsupported)}"
        )
    else:
        logging.warning(
            f"No downloader link found for {comic_url}. "
            f"(HTTP {response.status_code}, page size {len(response.text)} bytes)"
        )

    return None
