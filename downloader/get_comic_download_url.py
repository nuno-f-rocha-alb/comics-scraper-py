from config import *
import requests
from bs4 import BeautifulSoup

def get_comic_download_url(comic_url):
    """Retrieves the encrypted downloader URL from the comic's page and then extracts the real URL from the Location header."""
    response = requests.get(comic_url, headers=HEADERS)
    soup = BeautifulSoup(response.text, 'html.parser')
    download_link = soup.find("a", title="DOWNLOAD NOW") or soup.find("a", title="Download Now")
    if download_link:
        encrypted_url = download_link['href']

        # Get the real downloader URL from the Location header
        download_response = requests.get(encrypted_url, headers=HEADERS, allow_redirects=False)
        if 'Location' in download_response.headers:
            return download_response.headers['Location']
    logging.warning(f"No downloader link found for {comic_url}.")
    return None