import time
import re

from config import *
from downloader.download_file import download_file
from downloader.get_comic_download_url import get_comic_download_url
from downloader.process_downloaded_comic import process_downloaded_comic
from util import normalize_title, extract_year_from_comic_title



def check_and_download_comics(entry, available_comics, local_dir):
    """Compares available comics with local files and downloads new ones if not already present, ignoring non-matching titles."""
    #if not os.path.exists(local_dir):
    #    os.makedirs(local_dir)

    # Normalize the series name for matching
    normalized_series_name = normalize_title(entry[1])

    # Define keywords to ignore
    ignore_keywords = ['Access', 'Preview', 'TPB']

    for title, comic_url in available_comics:

        # Normalize the title from the website for comparison
        normalized_title = normalize_title(title)

        # Extract the base title from the comic title (removing issue number and year)
        base_title_match = re.match(r"^(.*?)\s*#\d+\s*\(\d{4}\)", normalized_title)
        if base_title_match:
            base_title = base_title_match.group(1).strip()
        else:
            logging.info(f"Ignoring {title} as it does not have the expected format.")
            continue

        if base_title != normalized_series_name:
            logging.info(f"Ignoring {title} as it does not match the series name {entry[1]}.")
            continue

        # Check if the normalized series name is part of the normalized title
        if normalized_series_name not in normalized_title:
            logging.info(f"Ignoring {title} as it does not match the series name {entry[1]}.")
            continue

        # Extract the year from the comic title
        year_match = extract_year_from_comic_title(title)
        if year_match is None:
            logging.info(f"Year not found in title: {title}. Ignoring.")
            continue

        comic_year = int(year_match)


        # Pattern to match existing files in the local directory
        existing_files = {f for f in os.listdir(local_dir)}

        # Compare the comic year against the directory year
        if comic_year < int(entry[2]):
            logging.info(f"Ignoring {title} as its year {comic_year} is older than the directory year {entry[2]}.")
            continue  # Ignore this comic as it is older than the directory year

        # Check for unwanted keywords in the title
        if any(keyword in title for keyword in ignore_keywords):
            logging.info(f"Ignoring {title} due to unwanted keyword in title.")
            continue

        # Extract and format the issue number
        issue_match = re.search(r"#(\d+)", title)
        issue_number = issue_match.group(1) if issue_match else "000"
        formatted_issue_number = f"{int(issue_number):03}" if issue_number.isdigit() else "000"



        # Regex to check for the existence of this issue in the local directory
        # The pattern accounts for variations in the file name while checking for the issue number
        comic_file_regex = re.compile(
            fr"^{re.escape(entry[1])}\s*#{formatted_issue_number}\s*.*\.(cbr|cbz)$",
            re.IGNORECASE
        )

        # Check if the exact issue already exists
        if not any(comic_file_regex.match(file) for file in existing_files):
            logging.info(f"New comic found: {title}. Downloading...")
            download_url = get_comic_download_url(comic_url)
            if download_url:
                save_path = download_file(download_url, local_dir, entry[1], formatted_issue_number)
                process_downloaded_comic(entry, save_path, issue_number)
            else:
                logging.warning(f"Download link not found for {title}.")
            time.sleep(1)
        else:
            logging.info(f"{title} already exists locally in an alternate format.")