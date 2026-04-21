import json
import os
from config import CACHE_FILE_PATH


def load_cache(series_name):
    """Return cached (title, url) pairs for a series, or empty list if none."""
    if not os.path.exists(CACHE_FILE_PATH):
        return []
    try:
        with open(CACHE_FILE_PATH) as f:
            data = json.load(f)
        return [tuple(entry) for entry in data.get(series_name, [])]
    except (json.JSONDecodeError, OSError):
        return []


def save_cache(series_name, comics):
    """Persist (title, url) pairs for a series."""
    data = {}
    if os.path.exists(CACHE_FILE_PATH):
        try:
            with open(CACHE_FILE_PATH) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    data[series_name] = [list(entry) for entry in comics]
    with open(CACHE_FILE_PATH, "w") as f:
        json.dump(data, f, indent=2)
