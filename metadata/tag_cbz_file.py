from datetime import datetime
from comicapi.comicarchive import ComicArchive
from comicapi.genericmetadata import GenericMetadata
from config import *


def tag_cbz_file(cbz_path, metadata):
    comic = ComicArchive(cbz_path)
    meta = GenericMetadata()

    meta.series = metadata.get("series_name", "")
    meta.issue = metadata.get("issue_number", "")
    meta.title = metadata.get("title", "")
    meta.publisher = metadata.get("publisher", "")
    meta.synopsis = metadata.get("description", "")
    meta.issue_count = metadata.get("issue_count", 0)

    store_date = metadata.get("store_date", "")
    if store_date:
        date_obj = datetime.strptime(store_date, "%Y-%m-%d")
        meta.day = date_obj.day
        meta.month = date_obj.month
        meta.year = date_obj.year

    logging.info(
        f"Writing metadata to {cbz_path}: Series: '{meta.series}', Issue: '{meta.issue}', "
        f"Title: '{meta.title}', Publisher: '{meta.publisher}', "
        f"Synopsis: '{meta.synopsis}', Issue Count: {meta.issue_count}, "
        f"Date: {getattr(meta, 'day', None)}-{getattr(meta, 'month', None)}-{getattr(meta, 'year', None)}"
    )

    if comic.write_metadata(meta, 1):
        logging.info(f"Tagged {cbz_path} successfully.")
    else:
        logging.error(f"Failed to tag {cbz_path}.")
