import logging
import os
from datetime import datetime

SERIES_FILE_PATH = "/app/comics/series_list.txt"  # Update with your series list file
CACHE_FILE_PATH = "/app/cache/search_cache.json"
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FOLDER = f"logs/"
LOG_FILENAME = f"comic_downloader_{CURRENT_DATE}.log"
os.makedirs(LOG_FOLDER, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_FOLDER, LOG_FILENAME)),
        logging.StreamHandler()  # This will output to the console as well
    ]
)

BASE_SEARCH_URL = "https://getcomics.org/page/{}/?s="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
COMICS_BASE_DIR = "comics"
API_KEY = "765ec8fbf2459db8276d47ed11f3ba74f961f018"
BASE_URL = "https://comicvine.gamespot.com/api"

# Define a User-Agent
HEADERS_APP = {
    'User-Agent': 'ComicApp/1.0 (https://comics.bifesserver.site)',
}

PUID = int(os.getenv("PUID", 1000))
PGID = int(os.getenv("PGID", 1000))

METRON_BASE_URL = "https://metron.cloud/api"
METRON_USER = os.getenv("METRON_USER", "")
METRON_PASS = os.getenv("METRON_PASS", "")
