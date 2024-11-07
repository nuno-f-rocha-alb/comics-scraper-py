import logging
import os
from datetime import datetime

SERIES_FILE_PATH = "series_list.txt"  # Update with your series list file
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
HEADERS = {"User-Agent": "Mozilla/5.0"}
COMICS_BASE_DIR = "comics"
API_KEY = "765ec8fbf2459db8276d47ed11f3ba74f961f018"
BASE_URL = "https://comicvine.gamespot.com/api"

# Define a User-Agent
HEADERS_APP = {
    'User-Agent': 'ComicApp/1.0 (https://comics.bifesserver.site)',
}