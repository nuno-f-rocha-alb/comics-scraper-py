from config import *
import time
from urllib.parse import urlparse
import requests
from tqdm_loggable.auto import tqdm
from util import sanitize_filename


class DownloadCancelled(Exception):
    """Raised when an in-flight download is cancelled by the user."""


# Magic-byte signatures per extension. A Cloudflare (or other) challenge page
# served with a 200 status still ends up here with a comic-file extension, so
# checking Content-Length/status alone isn't enough — sniff the actual bytes.
_MAGIC_SIGNATURES = {
    ".cbz": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".cbr": (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00"),
    ".pdf": (b"%PDF",),
}


def _looks_like_expected_file(path, file_extension):
    """True if path's header matches file_extension's known signature, or the
    extension isn't one we recognise (be lenient rather than reject unknowns)."""
    signatures = _MAGIC_SIGNATURES.get(file_extension.lower())
    if not signatures:
        return True
    with open(path, "rb") as f:
        header = f.read(8)
    return any(header.startswith(sig) for sig in signatures)


def download_file(url, save_dir, series_name, issue_number, volume_year,
                  is_cancelled=None, on_progress=None):
    """Downloads a file from the final URL and renames it based on the series name and issue number.

    is_cancelled: optional callable returning True to abort the download mid-stream.
    on_progress: optional callback (bytes_written, total_size) called every
                 ~64 chunks (~0.5MB) — surfaces progress to the UI in real time.
    Both checked at the same cadence so the per-chunk overhead is negligible.
    """
    parsed_url = urlparse(url)
    file_name = os.path.basename(parsed_url.path)
    file_extension = os.path.splitext(file_name)[1]  # Get the file extension

    # `with` guarantees the streaming connection/socket is released on every
    # exit path (success, IOError, or mid-download cancel).
    with requests.get(url, headers=HEADERS, stream=True, timeout=(10, 120)) as response:
        response.raise_for_status()

        total_size = int(response.headers.get('Content-Length', 0))

        safe_name = sanitize_filename(series_name)
        save_path = os.path.join(save_dir, f"{safe_name} #{issue_number} ({volume_year}){file_extension}")
        part_path = save_path + ".part"

        bytes_written = 0
        chunks_seen = 0
        download_start_time = time.time()
        try:
            with open(part_path, 'wb') as f:
                # Create a tqdm progress bar
                with tqdm(total=total_size, unit='B', unit_scale=True, desc=f"Downloading {series_name} #{issue_number} ({volume_year}){file_extension}") as pbar:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        bytes_written += len(chunk)
                        chunks_seen += 1
                        pbar.update(len(chunk))
                        if chunks_seen % 64 == 0:
                            if is_cancelled and is_cancelled():
                                raise DownloadCancelled("cancelled mid-download")
                            if on_progress:
                                try:
                                    on_progress(bytes_written, total_size)
                                except Exception:
                                    pass  # progress reporting must never break a download
            download_end_time = time.time()

            if total_size and bytes_written != total_size:
                raise IOError(
                    f"Incomplete download: got {bytes_written} bytes, expected {total_size}"
                )

            if not _looks_like_expected_file(part_path, file_extension):
                raise IOError(
                    f"Downloaded content for {save_path} doesn't look like a "
                    f"{file_extension} file (got a challenge/error page instead? "
                    f"possibly blocked by Cloudflare)"
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
