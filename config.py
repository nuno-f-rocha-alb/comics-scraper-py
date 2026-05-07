import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

SERIES_FILE_PATH = "/app/comics/series_list.txt"  # Update with your series list file
CACHE_FILE_PATH = "/app/cache/search_cache.json"
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FOLDER = f"logs/"
LOG_FILENAME = "comic_downloader.log"
os.makedirs(LOG_FOLDER, exist_ok=True)

_LOG_FMT = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
_root = logging.getLogger()
_root.setLevel(logging.INFO)

# Add handlers explicitly so they work even when uvicorn has already
# configured the root logger (logging.basicConfig is a no-op in that case).
if not any(isinstance(h, logging.FileHandler) for h in _root.handlers):
    _fh = TimedRotatingFileHandler(
        os.path.join(LOG_FOLDER, LOG_FILENAME),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    _fh.suffix = "%Y-%m-%d.log"
    _fh.setFormatter(_LOG_FMT)
    _root.addHandler(_fh)

if not any(
    isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    for h in _root.handlers
):
    _sh = logging.StreamHandler()
    _sh.setFormatter(_LOG_FMT)
    _root.addHandler(_sh)

BASE_SEARCH_URL = "https://getcomics.org/page/{}/?s="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
COMICS_BASE_DIR = "comics"
API_KEY = os.getenv("COMICVINE_API_KEY", "")
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
