# Comics Scraper — Claude Context

Dockerized Python scraper that downloads comics from getcomics.org, tags them with ComicVine metadata, and saves to `/app/comics`.

## How it runs
Single Docker container. Entrypoint loops: run `main.py`, sleep 24h, repeat.
`docker-compose.yml` mounts:
- `/newStellarvault/Comics` → `/app/comics` (series_list.txt lives here)
- `./logs` → `/app/logs`
- `./cache` → `/app/cache` (search_cache.json lives here)

## series_list.txt format
```
Publisher/Series Name/Year/ComicVineVolumeID/AnnualVolumeID
```
- `AnnualVolumeID` is optional — if present, annuals are downloaded to `{series}/Annuals/`
- Lines starting with `#` are treated as comments

## Key decisions already made
- `normalize_title()` lowercases before prefix stripping — fixes site title-case changes breaking matches
- `_resolve_url()` in `get_comic_download_url.py` skips HTTP check for `.cbz/.cbr` URLs (avoids timing out on large file downloads)
- Search cache (`/app/cache/search_cache.json`) stores `seen_urls` (ALL URLs encountered, not just filtered ones) so broad searches like "The Darkness" (97 pages) stop early on re-runs
- 2s delay + 30s backoff on 429 in `get_comic_download_url.py` to avoid rate limiting

## Next big task: Web Interface
Full plan is in memory. Key points:
- Stack: FastAPI + SQLAlchemy + SQLite + Bootstrap 5 + HTMX
- Replace series_list.txt with a DB; scraper reads from DB
- ComicVine search UI with cover art when adding series
- "Verify step": auto-test getcomics.org search name, user can edit before saving
- Annual auto-detection: link to parent series automatically
- APScheduler replaces the shell loop; single container

Start with Phase 1: DB schema + SQLAlchemy models + migration from series_list.txt.
