"""
Scan the local comics library and (re)tag CBZ files with Metron/ComicVine metadata.

Usage:
  python retag_comics.py                       # tag files missing metadata
  python retag_comics.py --force               # retag everything
  python retag_comics.py --dry-run             # show what would be tagged without writing
  python retag_comics.py --series "Batman"     # limit to one series (partial match)
"""
import argparse
import os
import re

from comicapi.comicarchive import ComicArchive

from config import COMICS_BASE_DIR, PUID, PGID
from metadata.get_comic_metadata import get_comic_metadata
from metadata.tag_cbz_file import tag_cbz_file
import logging


def has_metadata(cbz_path: str) -> bool:
    meta = ComicArchive(cbz_path).read_metadata(1)
    return bool(meta.series)


def _issue_number(filename: str) -> str | None:
    m = re.search(r"#(\d+)", filename)
    return str(int(m.group(1))) if m else None


def retag_directory(entry: tuple, directory: str, force: bool = False, dry_run: bool = False) -> tuple[int, int]:
    """Tag CBZ files in a directory. Returns (tagged, skipped)."""
    tagged = skipped = 0

    for filename in sorted(os.listdir(directory)):
        if not filename.lower().endswith((".cbz", ".cbr")):
            continue

        issue_number = _issue_number(filename)
        if not issue_number:
            logging.warning(f"Could not parse issue number from: {filename}")
            continue

        cbz_path = os.path.join(directory, filename)

        if not force and has_metadata(cbz_path):
            skipped += 1
            continue

        if dry_run:
            logging.info(f"[DRY RUN] Would tag: {cbz_path}")
            tagged += 1
            continue

        metadata = get_comic_metadata(entry, issue_number)
        if metadata:
            tag_cbz_file(cbz_path, metadata)
            os.chown(cbz_path, PUID, PGID)
            tagged += 1
        else:
            logging.warning(f"No metadata found for {filename}")

    return tagged, skipped


def retag_series(entry: tuple, force: bool = False, dry_run: bool = False):
    """Tag all CBZ files for a series entry (main issues + annuals if configured)."""
    from util import sanitize_filename
    series_dir = os.path.join(COMICS_BASE_DIR, sanitize_filename(entry[0]), f"{sanitize_filename(entry[1])} ({entry[2]})")

    if not os.path.exists(series_dir):
        logging.info(f"Not found locally, skipping: {series_dir}")
        return

    logging.info(f"Scanning: {entry[1]} ({entry[2]})")
    tagged, skipped = retag_directory(entry, series_dir, force, dry_run)
    logging.info(f"  Main — tagged: {tagged}, skipped: {skipped}")

    annual_volume_id = entry[4] if len(entry) > 4 else None
    if annual_volume_id:
        annuals_dir = os.path.join(series_dir, "Annuals")
        if os.path.exists(annuals_dir):
            annual_entry = (entry[0], f"{entry[1]} Annual", entry[2], annual_volume_id)
            tagged, skipped = retag_directory(annual_entry, annuals_dir, force, dry_run)
            logging.info(f"  Annuals — tagged: {tagged}, skipped: {skipped}")


def load_series_from_db() -> list[tuple]:
    """Load all enabled series from the DB and return as scraper tuples."""
    from web.database import SessionLocal
    from web.models import Series
    db = SessionLocal()
    try:
        rows = db.query(Series).filter(Series.enabled == True).order_by(Series.publisher, Series.series_name).all()
        return [s.to_scraper_tuple() for s in rows]
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Retag comics with Metron/ComicVine metadata")
    parser.add_argument("--force", action="store_true", help="Retag even if metadata already exists")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be tagged without writing")
    parser.add_argument("--series", metavar="NAME", help="Only process series whose name contains NAME")
    args = parser.parse_args()

    series_list = load_series_from_db()

    if args.series:
        series_list = [e for e in series_list if args.series.lower() in e[1].lower()]
        if not series_list:
            logging.warning(f"No series matching '{args.series}' found in DB")
            return

    for entry in series_list:
        retag_series(entry, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
