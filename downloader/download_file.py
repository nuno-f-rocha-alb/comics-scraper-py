from config import *
import time
from urllib.parse import urlparse
import requests
from tqdm_loggable.auto import tqdm


def download_file(url, save_dir, series_name, issue_number):
    """Downloads a file from the final URL and renames it based on the series name and issue number."""
    parsed_url = urlparse(url)
    file_name = os.path.basename(parsed_url.path)
    file_extension = os.path.splitext(file_name)[1]  # Get the file extension

    response = requests.get(url, headers=HEADERS, stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get('Content-Length', 0))

    save_path = os.path.join(save_dir, f"{series_name} #{issue_number}{file_extension}")

    with open(save_path, 'wb') as f:
        # Create a tqdm progress bar
        with tqdm(total=total_size, unit='B', unit_scale=True, desc=f"Downloading {series_name} #{issue_number}{file_extension}") as pbar:
            download_start_time = time.time()
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                pbar.update(len(chunk))
            download_end_time = time.time()

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
