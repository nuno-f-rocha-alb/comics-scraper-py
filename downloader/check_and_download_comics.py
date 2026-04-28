import os
import time
import re
from datetime import datetime, timezone

from config import *
from downloader.download_file import download_file
from downloader.get_comic_download_url import get_comic_download_url
from downloader.process_downloaded_comic import process_downloaded_comic
from util import normalize_title, extract_year_from_comic_title


def _record_job(series_id, issue_number, search_term, status, filename=None, error=None):
    """Write a DownloadJob record to the DB. Silent no-op if series_id is None."""
    if series_id is None:
        return
    try:
        from web.database import SessionLocal
        from web.models import DownloadJob
        with SessionLocal() as db:
            db.add(DownloadJob(
                series_id=series_id,
                issue_number=issue_number,
                search_term=search_term,
                status=status,
                source="scraper",
                filename=os.path.basename(filename) if filename else None,
                error=error,
                finished_at=datetime.now(timezone.utc),
            ))
            db.commit()
    except Exception as exc:
        logging.warning("Failed to record download job: %s", exc)


def check_and_download_comics(
    entry,
    available_comics,
    local_dir,
    *,
    series_id=None,
    monitored_regular=None,
    monitored_annual=None,
    # Legacy kwarg kept for callers that haven't been updated yet
    monitored_set=None,
):
    """Compare available comics with local files and download new ones.

    Downloads all new issues first, then processes metadata in batch at the end.
    series_id: DB id used to write DownloadJob records (None = don't record).
    monitored_regular / monitored_annual: frozenset of issue numbers to download per type.
      None means "all" (default); an empty set means "none selected".
    monitored_set: legacy single-set kwarg; used only if the typed sets are both None.
    """

    # Back-compat: if old callers pass monitored_set, apply it to both types
    if monitored_regular is None and monitored_annual is None and monitored_set is not None:
        monitored_regular = monitored_set
        monitored_annual  = monitored_set

    normalized_series_name = normalize_title(entry[1])
    normalized_annual_name = normalized_series_name + " annual"

    annual_volume_id = entry[4] if len(entry) > 4 else None
    issue_min = entry[7] if len(entry) > 7 and entry[7] is not None else 1
    issue_max = entry[8] if len(entry) > 8 and entry[8] is not None else None

    ignore_keywords = ['Access', 'Preview', 'TPB']

    existing_files = {f for f in os.listdir(local_dir)}
    annuals_dir = os.path.join(local_dir, "Annuals")
    existing_annual_files = None

    # Collect all (entry, save_path, issue_number) to process metadata in batch
    downloaded: list[tuple] = []

    for title, comic_url in available_comics:

        normalized_title = normalize_title(title)

        base_title_match = re.match(r"^(.*?)\s*#([\d.]+(?:\.\w+)?)\s*\(\d{4}\)", normalized_title)
        if base_title_match:
            base_title = base_title_match.group(1).strip()
        else:
            logging.info(f"Ignoring {title} as it does not have the expected format.")
            continue

        base_title_clean = ' '.join(re.sub(r'\b\d{4}\b', '', base_title).split())

        is_main   = base_title == normalized_series_name
        is_annual = base_title == normalized_annual_name or base_title_clean == normalized_annual_name

        if not is_main and not is_annual:
            logging.info(f"Ignoring {title} as it does not match the series name {entry[1]}.")
            continue

        if is_annual and not annual_volume_id:
            logging.info(f"Ignoring {title} (annual) as no annual volume ID is configured.")
            continue

        if normalized_series_name not in normalized_title:
            logging.info(f"Ignoring {title} as it does not match the series name {entry[1]}.")
            continue

        year_match = extract_year_from_comic_title(title)
        if year_match is None:
            logging.info(f"Year not found in title: {title}. Ignoring.")
            continue

        comic_year = int(year_match)
        if comic_year < int(entry[2]):
            logging.info(f"Ignoring {title} as its year {comic_year} is older than the directory year {entry[2]}.")
            continue

        if any(keyword in title for keyword in ignore_keywords):
            logging.info(f"Ignoring {title} due to unwanted keyword in title.")
            continue

        issue_match = re.search(r"#(\d+)", title)
        issue_number = issue_match.group(1) if issue_match else "000"
        formatted_issue_number = f"{int(issue_number):03}" if issue_number.isdigit() else "000"

        # Apply issue number bounds
        if issue_number.isdigit():
            num = int(issue_number)
            if issue_min is not None and num < issue_min:
                logging.info(f"Ignoring {title}: issue #{num} is below issue_min={issue_min}.")
                continue
            if issue_max is not None and num > issue_max:
                logging.info(f"Ignoring {title}: issue #{num} is above issue_max={issue_max}.")
                continue

        # Apply selective monitoring per type
        monitored = monitored_annual if is_annual else monitored_regular
        if monitored is not None:
            try:
                norm_num = str(int(float(issue_number)))
            except (ValueError, TypeError):
                norm_num = issue_number
            if norm_num not in monitored:
                logging.info(f"Ignoring {title}: issue #{issue_number} not in monitored set.")
                continue

        if is_annual:
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
                    _record_job(series_id, issue_number, title, "done", filename=save_path)
                    downloaded.append((annual_entry, save_path, issue_number))
                    existing_annual_files = {f for f in os.listdir(annuals_dir)}
                else:
                    logging.warning(f"Download link not found for {title}.")
                    _record_job(series_id, issue_number, title, "failed", error="Download link not found")
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
                    _record_job(series_id, issue_number, title, "done", filename=save_path)
                    downloaded.append((entry, save_path, issue_number))
                    existing_files = {f for f in os.listdir(local_dir)}
                else:
                    logging.warning(f"Download link not found for {title}.")
                    _record_job(series_id, issue_number, title, "failed", error="Download link not found")
                time.sleep(1)
            else:
                logging.info(f"{title} already exists locally in an alternate format.")

    # Process metadata for all downloaded issues in batch
    if downloaded:
        logging.info(f"Processing metadata for {len(downloaded)} downloaded issue(s)...")
        for proc_entry, save_path, issue_number in downloaded:
            process_downloaded_comic(proc_entry, save_path, issue_number)
