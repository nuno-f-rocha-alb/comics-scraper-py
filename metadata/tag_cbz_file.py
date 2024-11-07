from comicapi.comicarchive import ComicArchive
from comicapi.genericmetadata import GenericMetadata

from config import *

def tag_cbz_file(cbz_path, metadata):
    # Load the .cbz file into a ComicArchive instance
    comic = ComicArchive(cbz_path)
    meta = GenericMetadata()

    # Populate metadata fields from the fetched metadata
    meta.series = metadata.get('volume', {}).get('name', "")
    meta.issue = metadata.get('issue_number', "")
    meta.title = metadata.get('name', "")
    meta.publisher = metadata.get('publisher', "")
    meta.synopsis = metadata.get('description', "")
    meta.issue_count = metadata.get('issue_count', 0)

    # Get the store date and parse it
    store_date = metadata.get('store_date', "")
    if store_date:
        # Parse the store date into a datetime object
        date_obj = datetime.strptime(store_date, '%Y-%m-%d')  # Adjust format if needed
        # Extract day, month, year
        meta.day = date_obj.day
        meta.month = date_obj.month
        meta.year = date_obj.year

    # Log the metadata being written
    logging.info(f"Writing metadata to {cbz_path}: Series: '{meta.series}', Issue: '{meta.issue}', Title: '{meta.title}', Publisher: '{meta.publisher}', Synopsis: '{meta.synopsis}', Issue Count: {meta.issue_count}, Date: {meta.day}-{meta.month}-{meta.year}")

    # Write the metadata to the .cbz file
    if comic.write_metadata(meta, 1):
        logging.info(f"Tagged {cbz_path} successfully with ComicVine metadata.")
    else:
        logging.error(f"Failed to tag {cbz_path}.")