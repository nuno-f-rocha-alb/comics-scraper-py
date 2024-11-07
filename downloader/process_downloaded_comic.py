from config import *
from metadata.get_comic_metadata import get_comic_metadata
from metadata.tag_cbz_file import tag_cbz_file
from util import convert_cbr_to_cbz


def process_downloaded_comic(file_path, series_name, issue_number, starting_year):
    # Convert .cbr to .cbz if necessary
    if file_path.endswith(".cbr"):
        file_path = convert_cbr_to_cbz(file_path)

    # Fetch metadata from ComicVine
    metadata = get_comic_metadata(series_name, issue_number, starting_year)
    if metadata:
        # Tag the .cbz file
        tag_cbz_file(file_path, metadata)
    else:
        logging.warning(f"Metadata for {series_name} #{issue_number} not found.")