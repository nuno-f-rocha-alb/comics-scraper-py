import zipfile
import rarfile
import re
from config import *


def convert_cbr_to_cbz(cbr_path):
    cbz_path = cbr_path.replace(".cbr", ".cbz")
    with rarfile.RarFile(cbr_path) as rar:
        with zipfile.ZipFile(cbz_path, "w") as cbz:
            for file_info in rar.infolist():
                with rar.open(file_info) as file:
                    cbz.writestr(file_info.filename, file.read())
    os.remove(cbr_path)  # Optional: delete the original .cbr file
    logging.info(f"Converted {cbr_path} to {cbz_path}.")
    return cbz_path


def extract_year_from_comic_title(title):
    """Extracts the release year from the comic title if available, defaults to 'Unknown' otherwise."""

    if re.search(r'\(\d{4}-\d{4}\)', title):
        return None # Return None to indicate that this comic should be ignored

    year_match = re.search(r"\((\d{4})\)", title)
    return year_match.group(1) if year_match else None # Return the year if found


def create_series_directory(publisher, series_name, year):
    """Creates a directory path based on publisher, series title, and year."""
    publisher_dir = str(os.path.join(COMICS_BASE_DIR, publisher))
    series_dir = os.path.join(publisher_dir, f"{series_name} ({year})")
    os.makedirs(series_dir, exist_ok=True)
    logging.info(f"Created directory {series_dir}.")
    return str(series_dir)


def normalize_title(title):
    """Normalize the title by stripping common prefixes and converting to lowercase."""
    common_prefixes = ["the ", "a ", "an "]  # Add other prefixes as needed
    title = title.lower()
    for prefix in common_prefixes:
        if title.startswith(prefix):
            title = title[len(prefix):]
    title = title.replace("–", "-")  # Replace en dash with hyphen
    title = title.strip()  # Trim whitespace
    return title