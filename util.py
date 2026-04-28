import zipfile
import rarfile
import re
from config import *


def convert_cbr_to_cbz(cbr_path):
    cbz_path = cbr_path.replace(".cbr", ".cbz")
    with rarfile.RarFile(cbr_path) as rar:
        with zipfile.ZipFile(cbz_path, "w") as cbz:
            for file_info in rar.infolist():
                try:
                    with rar.open(file_info) as file:
                        cbz.writestr(file_info.filename, file.read())
                except Exception as e:
                    logging.error(f"Error converting {cbr_path} to {cbz_path}: {e}")
                    continue
    if os.path.exists(cbz_path) and os.path.getsize(cbz_path) > 0:
        os.remove(cbr_path)
        logging.info(f"Converted {cbr_path} to {cbz_path}.")
    else:
        logging.error(f"CBZ output missing or empty after conversion of {cbr_path}, keeping original.")
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

    Spaces around ':' are absorbed: "Batman: Year One" → "Batman-Year One"
    All other invalid chars (\\/*?"<>|;) → '-'
    Collapses consecutive dashes and trims.
    """
    name = re.sub(r'\s*:\s*', '-', name)   # "Batman: Year" → "Batman-Year"
    name = _INVALID_CHARS.sub("-", name)
    name = re.sub(r'-{2,}', '-', name)     # collapse consecutive dashes
    name = re.sub(r'\s{2,}', ' ', name)    # collapse double spaces
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