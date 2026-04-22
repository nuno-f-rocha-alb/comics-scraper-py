import time
import re

from config import *
from downloader.download_file import download_file
from downloader.get_comic_download_url import get_comic_download_url
from downloader.process_downloaded_comic import process_downloaded_comic
from util import normalize_title, extract_year_from_comic_title


def check_and_download_comics(entry, available_comics, local_dir):
    """Compares available comics with local files and downloads new ones if not already present, ignoring non-matching titles."""

    # Normalize the series name for matching
    normalized_series_name = normalize_title(entry[1])
    normalized_annual_name = normalized_series_name + " annual"

    # entry[4] is the optional annual ComicVine volume ID
    annual_volume_id = entry[4] if len(entry) > 4 else None

    # Define keywords to ignore
    ignore_keywords = ['Access', 'Preview', 'TPB']

    existing_files = {f for f in os.listdir(local_dir)}

    # Annuals directory and its file listing — only initialised if needed
    annuals_dir = os.path.join(local_dir, "Annuals")
    existing_annual_files = None

    for title, comic_url in available_comics:

        # Normalize the title from the website for comparison
        normalized_title = normalize_title(title)

        # Extract the base title from the comic title (removing issue number and year)
        base_title_match = re.match(r"^(.*?)\s*#([\d.]+(?:\.\w+)?)\s*\(\d{4}\)", normalized_title)
        if base_title_match:
            base_title = base_title_match.group(1).strip()
        else:
            logging.info(f"Ignoring {title} as it does not have the expected format.")
            continue

        # Strip bare years (e.g. "2026") from the base title before comparing —
        # some titles embed the year like "Absolute Wonder Woman 2026 Annual #1 (2026)"
        base_title_clean = ' '.join(re.sub(r'\b\d{4}\b', '', base_title).split())

        is_main = base_title == normalized_series_name
        is_annual = base_title == normalized_annual_name or base_title_clean == normalized_annual_name

        if not is_main and not is_annual:
            logging.info(f"Ignoring {title} as it does not match the series name {entry[1]}.")
            continue

        if is_annual and not annual_volume_id:
            logging.info(f"Ignoring {title} (annual) as no annual volume ID is configured.")
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

        # Compare the comic year against the directory year
        if comic_year < int(entry[2]):
            logging.info(f"Ignoring {title} as its year {comic_year} is older than the directory year {entry[2]}.")
            continue

        # Check for unwanted keywords in the title
        if any(keyword in title for keyword in ignore_keywords):
            logging.info(f"Ignoring {title} due to unwanted keyword in title.")
            continue

        # Extract and format the issue number
        issue_match = re.search(r"#(\d+)", title)
        issue_number = issue_match.group(1) if issue_match else "000"
        formatted_issue_number = f"{int(issue_number):03}" if issue_number.isdigit() else "000"

        if is_annual:
            # Prepare annuals directory on first use
            if existing_annual_files is None:
                os.makedirs(annuals_dir, exist_ok=True)
                os.chown(annuals_dir, PUID, PGID)
                existing_annual_files = {f for f in os.listdir(annuals_dir)}

            annual_series_name = entry[1] + " Annual"
            annual_entry = (entry[0], annual_series_name, entry[2], annual_volume_id)
            comic_file_regex = re.compile(
                fr"^{re.escape(annual_series_name)}\s*#{formatted_issue_number}\s*.*\.(cbr|cbz)$",
                re.IGNORECASE
            )

            if not any(comic_file_regex.match(f) for f in existing_annual_files):
                logging.info(f"New annual found: {title}. Downloading...")
                download_url = get_comic_download_url(comic_url)
                if download_url:
                    save_path = download_file(download_url, annuals_dir, annual_series_name, formatted_issue_number, entry[2])
                    process_downloaded_comic(annual_entry, save_path, issue_number)
                    existing_annual_files = {f for f in os.listdir(annuals_dir)}
                else:
                    logging.warning(f"Download link not found for {title}.")
                time.sleep(1)
            else:
                logging.info(f"{title} already exists locally in an alternate format.")

        else:
            comic_file_regex = re.compile(
                fr"^{re.escape(entry[1])}\s*#{formatted_issue_number}\s*.*\.(cbr|cbz)$",
                re.IGNORECASE
            )

            if not any(comic_file_regex.match(file) for file in existing_files):
                logging.info(f"New comic found: {title}. Downloading...")
                download_url = get_comic_download_url(comic_url)
                if download_url:
                    save_path = download_file(download_url, local_dir, entry[1], formatted_issue_number, entry[2])
                    process_downloaded_comic(entry, save_path, issue_number)
                    existing_files = {f for f in os.listdir(local_dir)}
                else:
                    logging.warning(f"Download link not found for {title}.")
                time.sleep(1)
            else:
                logging.info(f"{title} already exists locally in an alternate format.")
