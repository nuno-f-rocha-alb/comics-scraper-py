"""
One-time migration: imports series_list.txt into the SQLite database.

Usage (inside container):
    python migrate_series_list.py
    python migrate_series_list.py --dry-run
    python migrate_series_list.py --series-file /path/to/series_list.txt
"""

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="Migrate series_list.txt to SQLite DB")
    p.add_argument("--series-file", default="/app/comics/series_list.txt")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    return p.parse_args()


def read_txt(path: str) -> list[dict]:
    entries = []
    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("/", 4)
            if len(parts) < 3:
                log.warning("Skipping invalid line: %s", line)
                continue

            publisher, series_name, year = parts[0].strip(), parts[1].strip(), parts[2].strip()
            cv_id = int(parts[3].strip()) if len(parts) >= 4 and parts[3].strip() else None
            annual_id = int(parts[4].strip()) if len(parts) == 5 and parts[4].strip() else None

            entries.append({
                "publisher": publisher,
                "series_name": series_name,
                "year": int(year) if year else None,
                "comicvine_volume_id": cv_id,
                "annual_comicvine_volume_id": annual_id,
            })
    return entries


def main():
    args = parse_args()

    from web.database import init_db, SessionLocal
    from web.models import Series
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    init_db()

    entries = read_txt(args.series_file)
    log.info("Found %d entries in %s", len(entries), args.series_file)

    if args.dry_run:
        for e in entries:
            log.info("  [dry-run] %s / %s (%s)  cv=%s  annual_cv=%s",
                     e["publisher"], e["series_name"], e["year"],
                     e["comicvine_volume_id"], e["annual_comicvine_volume_id"])
        return

    inserted = skipped = 0
    with SessionLocal() as session:
        for e in entries:
            stmt = (
                sqlite_insert(Series)
                .values(**e)
                .on_conflict_do_nothing()
            )
            result = session.execute(stmt)
            if result.rowcount:
                inserted += 1
            else:
                skipped += 1
                log.info("Already exists, skipped: %s / %s (%s)",
                         e["publisher"], e["series_name"], e["year"])
        session.commit()

    log.info("Done — %d inserted, %d skipped (already existed).", inserted, skipped)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)
