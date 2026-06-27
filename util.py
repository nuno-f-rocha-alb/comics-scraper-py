import shutil
import zipfile
import rarfile
import re
from urllib.parse import urlparse
from config import *


def staging_dir():
    """Hidden staging folder inside the comics volume (same filesystem as the
    library, so the final landing is an atomic rename). Created on demand."""
    path = os.path.join(COMICS_BASE_DIR, STAGING_SUBDIR)
    os.makedirs(path, exist_ok=True)
    os.chown(path, PUID, PGID)
    return path


def install_to_library(staged_path, dest_dir):
    """Move a finished comic from staging into the library without ever exposing
    a partial file to Komga. Returns the final path."""
    os.makedirs(dest_dir, exist_ok=True)
    os.chown(dest_dir, PUID, PGID)
    final = os.path.join(dest_dir, os.path.basename(staged_path))
    # shutil.move handles staging/dest on different mergerfs branches; the hidden
    # dot-temp + os.replace guarantees the visible final name appears atomically.
    tmp = os.path.join(dest_dir, "." + os.path.basename(staged_path) + ".tmp")
    shutil.move(staged_path, tmp)
    os.replace(tmp, final)  # overwrites an existing file on re-download
    os.chown(final, PUID, PGID)
    return final


def is_getcomics_url(u: str | None) -> bool:
    """True only for https://getcomics.org (or *.getcomics.org) URLs.

    Guards the worker's direct-download path against SSRF: a stored job url is
    later fetched server-side, so a client-supplied url must be allowlisted.
    """
    if not u:
        return False
    try:
        p = urlparse(u)
    except (ValueError, TypeError):
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == "getcomics.org" or host.endswith(".getcomics.org"))


def convert_cbr_to_cbz(cbr_path):
    cbz_path = os.path.splitext(cbr_path)[0] + ".cbz"
    written = 0
    failed = 0
    with rarfile.RarFile(cbr_path) as rar:
        with zipfile.ZipFile(cbz_path, "w") as cbz:
            for file_info in rar.infolist():
                try:
                    with rar.open(file_info) as file:
                        cbz.writestr(file_info.filename, file.read())
                        written += 1
                except Exception as e:
                    logging.error(f"Error converting {cbr_path} to {cbz_path}: {e}")
                    failed += 1
                    continue
    # Only delete the source CBR on a fully clean conversion. A partial one
    # (any entry failed) keeps the original — deleting it would lose the pages
    # that didn't make it into the CBZ. (Empty zips are ~22 bytes, so we count
    # writes, not file size.)
    if written > 0 and failed == 0 and os.path.exists(cbz_path):
        os.remove(cbr_path)
        logging.info(f"Converted {cbr_path} to {cbz_path}.")
    else:
        logging.error(
            f"Conversion of {cbr_path} incomplete ({written} ok, {failed} failed) "
            f"or output missing — keeping original."
        )
    return cbz_path


def extract_year_from_comic_title(title):
    """Extracts the release year from the comic title if available, defaults to 'Unknown' otherwise."""

    if re.search(r'\(\d{4}-\d{4}\)', title):
        return None # Return None to indicate that this comic should be ignored

    year_match = re.search(r"\((\d{4})\)", title)
    return year_match.group(1) if year_match else None # Return the year if found


def create_series_directory(entry):
    """Creates a directory path based on publisher, series title, and year."""
    publisher_dir = str(os.path.join(COMICS_BASE_DIR, sanitize_filename(entry[0])))
    series_dir = os.path.join(publisher_dir, f"{sanitize_filename(entry[1])} ({entry[2]})")
    is_new = not os.path.exists(series_dir)
    os.makedirs(series_dir, exist_ok=True)

    # Change ownership
    os.chown(series_dir, PUID, PGID)
    os.chown(publisher_dir, PUID, PGID)

    if is_new:
        logging.info(f"Created directory {series_dir}.")
    return str(series_dir)


_INVALID_CHARS = re.compile(r'[\\/*?"<>|;]')


def sanitize_filename(name: str) -> str:
    """Replace OS-invalid characters so names are safe on Windows and Linux.

    ':' and '/' (common Metron separators) → ' - ' to match the getcomics
    naming convention: "Batman/Superman: World's Finest" → "Batman - Superman - World's Finest"
    All other invalid chars (\\*?"<>|;) → '-'
    Collapses consecutive separators and trims.
    """
    name = re.sub(r'\s*:\s*', ' - ', name)   # "Batman: Year One" → "Batman - Year One"
    name = re.sub(r'\s*/\s*', ' - ', name)   # "Batman/Superman" → "Batman - Superman"
    name = _INVALID_CHARS.sub("-", name)
    name = re.sub(r' - - ', ' - ', name)     # collapse doubled separators
    name = re.sub(r'-{2,}', '-', name)       # collapse consecutive dashes
    name = re.sub(r' {2,}', ' ', name)       # collapse double spaces
    return name.strip(" -")


def normalize_title(title):
    """Normalize a title for comparison: lowercase, strip common prefixes,
    and reduce separators (en-dash, colon, slash) to ' - ' so that
    'Batman/Superman: World's Finest' and 'Batman – Superman – World's Finest'
    both become 'batman - superman - world's finest'."""
    title = title.lower()
    common_prefixes = ["the ", "a ", "an "]
    for prefix in common_prefixes:
        if title.startswith(prefix):
            title = title[len(prefix):]
    title = title.replace("–", "-")          # en dash → hyphen (keeps spaces)
    title = re.sub(r'\s*/\s*', ' - ', title)  # "/" → " - "
    title = re.sub(r'\s*:\s*', ' - ', title)  # ":" → " - "
    title = re.sub(r' {2,}', ' ', title)      # collapse double spaces
    return title.strip()