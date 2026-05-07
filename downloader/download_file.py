from config import *
import time
from urllib.parse import urlparse
import requests
from tqdm_loggable.auto import tqdm
from util import sanitize_filename


def download_file(url, save_dir, series_name, issue_number, volume_year):
    """Downloads a file from the final URL and renames it based on the series name and issue number."""
    parsed_url = urlparse(url)
    file_name = os.path.basename(parsed_url.path)
    file_extension = os.path.splitext(file_name)[1]  # Get the file extension

    response = requests.get(url, headers=HEADERS, stream=True, timeout=(10, 120))
    response.raise_for_status()

    total_size = int(response.headers.get('Content-Length', 0))

    safe_name = sanitize_filename(series_name)
    save_path = os.path.join(save_dir, f"{safe_name} #{issue_number} ({volume_year}){file_extension}")
    part_path = save_path + ".part"

    bytes_written = 0
    download_start_time = time.time()
    try:
        with open(part_path, 'wb') as f:
            # Create a tqdm progress bar
            with tqdm(total=total_size, unit='B', unit_scale=True, desc=f"Downloading {series_name} #{issue_number} ({volume_year}){file_extension}") as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    bytes_written += len(chunk)
                    pbar.update(len(chunk))
        download_end_time = time.time()

        if total_size and bytes_written != total_size:
            raise IOError(
                f"Incomplete download: got {bytes_written} bytes, expected {total_size}"
            )

        if os.path.exists(save_path):
            os.remove(save_path)
        os.rename(part_path, save_path)
    except BaseException:
        try:
            if os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass
        raise

    download_elapsed_time = download_end_time - download_start_time

    # Convert elapsed time to minutes and seconds
    download_minutes = int(download_elapsed_time // 60)
    download_seconds = download_elapsed_time % 60

    # Log the elapsed time in minutes and seconds if it's more than 60 seconds
    if download_minutes > 0:
        logging.info(f"Downloaded and saved as {save_path} in {download_minutes} minutes and {download_seconds:.2f} seconds")
    else:
        logging.info(f"Downloaded and saved as {save_path} in {download_seconds:.2f} seconds")

    return save_path
