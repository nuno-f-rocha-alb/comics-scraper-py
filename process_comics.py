import os
import re
import sys
import logging
from config import *
from metadata.get_comic_metadata import get_comic_metadata
from metadata.tag_cbz_file import tag_cbz_file
from util import convert_cbr_to_cbz, normalize_title

def extract_year_from_folder(folder_name):
    """Extract the year from the folder name."""
    match = re.search(r'\((\d{4})\)', folder_name)
    return match.group(1) if match else None

def extract_issue_number(file_name):
    """Extract the issue number from the file name."""
    parts = file_name.split()
    for part in parts:
        if re.match(r'^\d+(\.\d+|\.[a-zA-Z]+|[a-zA-Z]+)?$', part):
            return part
    return None

def rename_comic_file(file_path, series_name, issue_number):
    """Rename the comic file to the specified layout."""
    folder_path = os.path.dirname(file_path)
    ext = os.path.splitext(file_path)[1]
    new_file_name = f"{series_name} #{issue_number}{ext}"
    new_file_path = os.path.join(folder_path, new_file_name)
    os.rename(file_path, new_file_path)
    return new_file_path

def process_comics_folder(folder_path):
    """Process and tag comics in the specified folder with the correct metadata."""
    folder_name = os.path.basename(folder_path)
    year = extract_year_from_folder(folder_name)
    if not year:
        logging.error(f"Year not found in folder name: {folder_name}")
        return

    # Remove the year from the folder name to get the series name
    series_name = folder_name.replace(f" ({year})", "").strip()

    for file_name in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file_name)
        if not os.path.isfile(file_path):
            continue

        # Extract issue number from the file name
        base_name, ext = os.path.splitext(file_name)
        if ext.lower() not in ['.cbr', '.cbz']:
            logging.warning(f"Skipping unsupported file format: {file_name}")
            continue

        # Assuming the file name format is "ComicTitle issueNumber (year) someGibberish.ext"
        issue_number = extract_issue_number(base_name)
        if not issue_number:
            logging.warning(f"Skipping file with unexpected name format: {file_name}")
            continue

        # Convert .cbr to .cbz if necessary
        if ext.lower() == '.cbr':
            file_path = convert_cbr_to_cbz(file_path)

        # Rename the comic file
        file_path = rename_comic_file(file_path, series_name, issue_number)

        # Fetch metadata from ComicVine
        issue_number = re.sub(r'^0+(?=\d)', '', issue_number)
        metadata = get_comic_metadata(series_name, issue_number, year)
        if metadata:
            # Tag the .cbz file
            tag_cbz_file(file_path, metadata)
        else:
            logging.warning(f"Metadata for {series_name} #{issue_number} ({year}) not found.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python process_comics.py <folder_path>")
        sys.exit(1)

    folder_path = sys.argv[1]
    process_comics_folder(folder_path)