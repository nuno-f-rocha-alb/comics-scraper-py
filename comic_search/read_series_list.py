from config import *

def read_series_list(file_path):
    """Reads series entries with publisher, series name, year, and optional comicvine_id from a text file."""
    series_list = []
    with open(file_path, 'r') as f:
        for line in f.readlines():
            # Split by the first three slashes (publisher/series_name/year/comicvine_volume_id)
            parts = line.strip().split("/", 3)
            if len(parts) == 4:
                publisher, series_name, year, comicvine_volume_id = parts
                series_list.append((publisher.strip(), series_name.strip(), year.strip(), comicvine_volume_id.strip()))
            elif len(parts) == 3:
                publisher, series_name, year = parts
                series_list.append((publisher.strip(), series_name.strip(), year.strip(), None))
            else:
                # Handle case where the format doesn't have 3 or 4 parts
                logging.warning(f"Skipping invalid entry: {line.strip()}")
    logging.info(f"Read {len(series_list)} series from {file_path}.")
    return series_list