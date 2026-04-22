import json
import os
from config import CACHE_FILE_PATH


def load_cache(series_name):
    """Return (seen_urls, comics) for a series.

    seen_urls: set of all URLs ever encountered during search (including filtered-out ones)
    comics: list of (title, url) tuples that passed the year filter
    """
    if not os.path.exists(CACHE_FILE_PATH):
        return set(), []
    try:
        with open(CACHE_FILE_PATH) as f:
            data = json.load(f)
        entry = data.get(series_name, {})
        seen_urls = set(entry.get("seen_urls", []))
        comics = [tuple(c) for c in entry.get("comics", [])]
        return seen_urls, comics
    except (json.JSONDecodeError, OSError):
        return set(), []


def save_cache(series_name, seen_urls, comics):
    """Persist seen_urls and (title, url) comics for a series."""
    data = {}
    if os.path.exists(CACHE_FILE_PATH):
        try:
            with open(CACHE_FILE_PATH) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    data[series_name] = {
        "seen_urls": list(seen_urls),
        "comics": [list(c) for c in comics],
    }
    os.makedirs(os.path.dirname(CACHE_FILE_PATH), exist_ok=True)
    with open(CACHE_FILE_PATH, "w") as f:
        json.dump(data, f, indent=2)
