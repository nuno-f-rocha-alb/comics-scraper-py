import os
import time
import errno
import logging
from config import *

def read_series_list(file_path):
    """
    Reads series entries with publisher, series name, year, and optional comicvine_id from a text file.
    Includes retry logic to handle Stale File Handles (Error 116) caused by Docker/NFS mounts.
    """
    max_retries = 3
    retry_delay = 2  # Seconds

    for attempt in range(max_retries):
        try:
            # FORCE REFRESH:
            # Listing the parent directory triggers the OS to refresh the file cache
            # and look for the new inode, which fixes the stale handle issue.
            try:
                os.listdir(os.path.dirname(file_path))
            except OSError:
                pass

            series_list = []
            
            with open(file_path, 'r') as f:
                for line in f.readlines():
                    # Skip empty lines to prevent errors
                    if not line.strip():
                        continue

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
            
            # If we reached here, the file was read successfully
            logging.info(f"Read {len(series_list)} series from {file_path}.")
            return series_list

        except OSError as e:
            # Check specifically for Stale File Handle (Errno 116)
            if e.errno == errno.ESTALE:
                logging.warning(f"Stale file handle detected for {file_path}. Retrying ({attempt + 1}/{max_retries})...")
                time.sleep(retry_delay)
                continue
            else:
                # If it's a different error (e.g., Permission denied), crash immediately
                logging.error(f"Failed to read series list due to unexpected error: {e}")
                raise e

    # If the loop finishes without returning, we failed all retries
    error_msg = f"Could not recover from stale file handle for {file_path} after {max_retries} attempts."
    logging.error(error_msg)
    raise OSError(errno.ESTALE, error_msg)

#from config import *
#
#def read_series_list(file_path):
#    """Reads series entries with publisher, series name, year, and optional comicvine_id from a text file."""
#    series_list = []
#    with open(file_path, 'r') as f:
#        for line in f.readlines():
#            # Split by the first three slashes (publisher/series_name/year/comicvine_volume_id)
#            parts = line.strip().split("/", 3)
#            if len(parts) == 4:
#                publisher, series_name, year, comicvine_volume_id = parts
#                series_list.append((publisher.strip(), series_name.strip(), year.strip(), comicvine_volume_id.strip()))
#            elif len(parts) == 3:
#                publisher, series_name, year = parts
#                series_list.append((publisher.strip(), series_name.strip(), year.strip(), None))
#            else:
#                # Handle case where the format doesn't have 3 or 4 parts
#                logging.warning(f"Skipping invalid entry: {line.strip()}")
#    logging.info(f"Read {len(series_list)} series from {file_path}.")
#    return series_list
