from config import *

def read_series_list(file_path):
    """Reads series entries with publisher, series name, and version from a text file."""
    series_list = []
    with open(file_path, 'r') as f:
        for line in f.readlines():
            # Split by the first two slashes (publisher/series_name/version)
            parts = line.strip().split("/", 2)
            if len(parts) == 3:
                publisher, series_name, year = parts
                series_list.append((publisher.strip(), series_name.strip(), year.strip()))
            else:
                # Handle case where the format doesn't have 3 parts (e.g., missing version)
                logging.warning(f"Skipping invalid entry: {line.strip()}")
    logging.info(f"Read {len(series_list)} series from {file_path}.")
    return series_list