"""Background download worker.

A single daemon thread pulls job IDs from a queue and processes them one at a
time, updating the DownloadJob row in SQLite as status changes.

On startup, any jobs left in 'queued' or 'downloading' state (from a previous
process crash) are automatically re-enqueued.
"""
import logging
import os
import queue
import re
import threading
from datetime import datetime, timezone

_q: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None


def enqueue(job_id: int) -> None:
    _q.put(job_id)


def _download_issue(series, issue_number: str) -> str:
    import requests
    from bs4 import BeautifulSoup
    from config import BASE_SEARCH_URL, HEADERS
    from downloader.download_file import download_file
    from downloader.get_comic_download_url import get_comic_download_url
    from downloader.process_downloaded_comic import process_downloaded_comic
    from util import create_series_directory, normalize_title

    entry = series.to_scraper_tuple()
    search_name = entry[6] or entry[1]
    normalized_series = normalize_title(entry[1])

    try:
        target_num = str(int(float(issue_number)))
    except (ValueError, TypeError):
        target_num = issue_number

    def _find_in_page(html: str) -> tuple[str | None, str | None]:
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.select("div.post-info h1.post-title a"):
            title = link.get_text(strip=True)
            norm = normalize_title(title)
            # Extract base title (everything before "#N (YYYY)") for exact match
            base_m = re.match(r"^(.*?)\s*#[\d.]+.*?\(\d{4}\)", norm)
            if not base_m:
                continue
            base_title = base_m.group(1).strip()
            if base_title != normalized_series:
                continue
            num_m = re.search(r"#(\d+(?:\.\d+)?)", title)
            if not num_m:
                continue
            try:
                found_num = str(int(float(num_m.group(1))))
            except ValueError:
                found_num = num_m.group(1)
            if found_num != target_num:
                continue
            return link["href"], title
        return None, None

    # Precise search: series name + issue number
    # '#' must be percent-encoded — bare '#' is a URL fragment separator and
    # never reaches the server, so the issue number would be silently dropped
    term1 = f"{search_name} #{issue_number}"
    encoded1 = term1.replace(' ', '+').replace('#', '%23')
    resp = requests.get(
        f"{BASE_SEARCH_URL.format(1)}{encoded1}",
        headers=HEADERS, timeout=15,
    )
    comic_url, comic_title = None, None
    if resp.status_code == 200 and "No Results Found" not in resp.text:
        comic_url, comic_title = _find_in_page(resp.text)

    # Broader fallback: series name only
    if not comic_url:
        resp2 = requests.get(
            f"{BASE_SEARCH_URL.format(1)}{search_name.replace(' ', '+')}",
            headers=HEADERS, timeout=15,
        )
        if resp2.status_code == 200 and "No Results Found" not in resp2.text:
            comic_url, comic_title = _find_in_page(resp2.text)

    if not comic_url:
        raise Exception(
            f"Issue #{issue_number} of '{entry[1]}' not found on getcomics.org"
        )

    logging.info("Download worker: found '%s', fetching download link…", comic_title)

    download_url = get_comic_download_url(comic_url)
    if not download_url:
        raise Exception(f"No download link found on page: {comic_url}")

    try:
        formatted = f"{int(float(issue_number)):03}"
    except (ValueError, TypeError):
        formatted = issue_number

    local_dir = create_series_directory(entry)
    save_path = download_file(download_url, local_dir, entry[1], formatted, entry[2])
    process_downloaded_comic(entry, save_path, issue_number)

    return os.path.basename(save_path)


def _process(job_id: int) -> None:
    from web.database import SessionLocal
    from web.models import DownloadJob, Series

    with SessionLocal() as db:
        job = db.get(DownloadJob, job_id)
        if not job:
            return

        job.status = "downloading"
        db.commit()

        try:
            s = db.get(Series, job.series_id)
            if not s:
                raise Exception("Series not found in DB")
            filename = _download_issue(s, job.issue_number)
            job.status = "done"
            job.filename = filename
        except Exception as exc:
            logging.error("Download job %d failed: %s", job_id, exc)
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = datetime.now(timezone.utc)
            db.commit()


def _run() -> None:
    while True:
        job_id = _q.get()
        try:
            _process(job_id)
        finally:
            _q.task_done()


def start() -> None:
    global _thread
    _thread = threading.Thread(target=_run, daemon=True, name="download-worker")
    _thread.start()

    # Re-enqueue jobs left in-flight from a previous process crash
    from web.database import SessionLocal
    from web.models import DownloadJob

    with SessionLocal() as db:
        stuck = (
            db.query(DownloadJob)
            .filter(DownloadJob.status.in_(["queued", "downloading"]))
            .order_by(DownloadJob.created_at)
            .all()
        )
        for job in stuck:
            job.status = "queued"
        db.commit()
        for job in stuck:
            _q.put(job.id)

    if stuck:
        logging.info("Download worker: re-enqueued %d stuck jobs.", len(stuck))
    else:
        logging.info("Download worker: started.")
