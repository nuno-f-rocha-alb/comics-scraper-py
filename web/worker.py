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
import time
from datetime import datetime, timezone

_q: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None

# Job IDs the user has asked to cancel while the worker is mid-flight.
# Checked at the start of _process (before HTTP work begins) and inside
# download_file's chunk loop.
_cancel_requested: set[int] = set()
_cancel_lock = threading.Lock()

# Live download progress, in-memory only. Cleared when a job finishes (any
# status) or when the process restarts (in which case the job is also
# re-enqueued from scratch by start()).
#   job_id -> {bytes, total, rate_bps, started_at, last_at, last_bytes}
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


def _set_progress(job_id: int, bytes_written: int, total_size: int) -> None:
    """Record current byte counts for a job and roll a smoothed rate."""
    now = time.time()
    with _progress_lock:
        existing = _progress.get(job_id)
        if existing is None:
            _progress[job_id] = {
                "bytes": bytes_written,
                "total": total_size,
                "rate_bps": 0.0,
                "started_at": now,
                "last_at": now,
                "last_bytes": bytes_written,
            }
            return
        dt = max(now - existing["last_at"], 1e-3)
        db = max(bytes_written - existing["last_bytes"], 0)
        instant = db / dt
        # Exponential moving average so the displayed speed doesn't bounce
        # around the way the raw chunk-to-chunk delta would.
        existing["rate_bps"] = 0.6 * existing["rate_bps"] + 0.4 * instant
        existing["bytes"] = bytes_written
        existing["total"] = total_size
        existing["last_at"] = now
        existing["last_bytes"] = bytes_written


def get_progress(job_id: int) -> dict | None:
    with _progress_lock:
        p = _progress.get(job_id)
        return dict(p) if p else None


def _clear_progress(job_id: int) -> None:
    with _progress_lock:
        _progress.pop(job_id, None)


def enqueue(job_id: int) -> None:
    _q.put(job_id)


def request_cancel(job_id: int) -> None:
    """Mark a job to abort the next time the worker checks (between HTTP calls
    or every ~0.5MB of file download). Safe to call repeatedly."""
    with _cancel_lock:
        _cancel_requested.add(job_id)


def _is_cancelled(job_id: int) -> bool:
    with _cancel_lock:
        return job_id in _cancel_requested


def _clear_cancel(job_id: int) -> None:
    with _cancel_lock:
        _cancel_requested.discard(job_id)


def _download_issue(series, issue_number: str, is_cancelled=None, on_progress=None, post_url=None) -> str:
    from downloader.download_file import download_file, DownloadCancelled
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

    # RSS / Releases supply the exact getcomics post URL — skip the search and
    # resolve the download link straight from it (lighter, no search mismatch).
    if post_url:
        comic_url, comic_title = post_url, post_url
    else:
        comic_url, comic_title = _search_for_issue(
            search_name, normalized_series, issue_number, target_num, is_cancelled,
        )

    if is_cancelled and is_cancelled():
        raise DownloadCancelled("cancelled before resolving download URL")

    logging.info("Download worker: found '%s', fetching download link…", comic_title)

    download_url = get_comic_download_url(comic_url)
    if not download_url:
        raise Exception(f"No download link found on page: {comic_url}")

    # Preserve decimal issues (#1.5 → 001.5) the same way the scraper does in
    # check_and_download_comics.py, so 1.5 doesn't collapse onto issue 1's file.
    issue_str = str(issue_number)
    if "." in issue_str:
        int_part, frac = issue_str.split(".", 1)
        try:
            formatted = f"{int(int_part):03}.{frac}"
        except (ValueError, TypeError):
            formatted = issue_str
    elif issue_str.isdigit():
        formatted = f"{int(issue_str):03}"
    else:
        formatted = issue_str

    local_dir = create_series_directory(entry)
    save_path = download_file(
        download_url, local_dir, entry[1], formatted, entry[2],
        is_cancelled=is_cancelled,
        on_progress=on_progress,
    )
    process_downloaded_comic(entry, save_path, issue_number)

    return os.path.basename(save_path)


def _search_for_issue(search_name, normalized_series, issue_number, target_num, is_cancelled):
    """Resolve a getcomics post URL by searching (the back-catalog/manual path).
    Returns (comic_url, comic_title); raises if not found."""
    import requests
    from bs4 import BeautifulSoup
    from config import BASE_SEARCH_URL, HEADERS
    from downloader.download_file import DownloadCancelled
    from util import normalize_title

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

    if is_cancelled and is_cancelled():
        raise DownloadCancelled("cancelled before broader search")

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
            f"Issue #{issue_number} of '{search_name}' not found on getcomics.org"
        )
    return comic_url, comic_title


def _process(job_id: int) -> None:
    from web.database import SessionLocal
    from web.models import DownloadJob, Series
    from downloader.download_file import DownloadCancelled

    with SessionLocal() as db:
        job = db.get(DownloadJob, job_id)
        if not job:
            return

        # Cancelled while still in the queue — drop it without touching the network.
        if job.status == "cancelled" or _is_cancelled(job_id):
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            _clear_cancel(job_id)
            return

        job.status = "downloading"
        db.commit()

        try:
            s = db.get(Series, job.series_id)
            if not s:
                raise Exception("Series not found in DB")
            filename = _download_issue(
                s, job.issue_number,
                is_cancelled=lambda: _is_cancelled(job_id),
                on_progress=lambda b, t: _set_progress(job_id, b, t),
                post_url=job.url,
            )
            job.status = "done"
            job.filename = filename
        except DownloadCancelled as exc:
            logging.info("Download job %d cancelled: %s", job_id, exc)
            job.status = "cancelled"
        except Exception as exc:
            logging.error("Download job %d failed: %s", job_id, exc)
            job.status = "failed"
            job.error = str(exc)
        finally:
            _clear_cancel(job_id)
            _clear_progress(job_id)
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
