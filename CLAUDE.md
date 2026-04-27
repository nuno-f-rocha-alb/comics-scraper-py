# Comics Scraper — Claude Context

Dockerized Python scraper that downloads comics from getcomics.org, tags them with Metron metadata (ComicVine as fallback), and saves to `/app/comics`.

## How it runs
Single Docker container. `entrypoint.py` starts uvicorn (port 8000) + APScheduler.
APScheduler runs `main.py:run_scraper()` every `SCHEDULE_INTERVAL_HOURS` hours (default 24), immediately on startup.
`docker-compose.yml` mounts:
- `/newStellarvault/Comics` → `/app/comics`
- `./logs` → `/app/logs`
- `./cache` → `/app/cache` (search_cache.json + comics.db live here)

## Series storage
SQLite DB at `/app/cache/comics.db`, managed by SQLAlchemy (`web/models.py`).
`series_list.txt` is no longer used — migrate with `python migrate_series_list.py`.

### Series tuple shape (used internally by scraper)
`(publisher, series_name, year, cv_id, annual_cv_id, metron_series_id, getcomics_search_name)`
- `[6] getcomics_search_name` overrides `[1] series_name` as the getcomics.org search term and cache key

## Key decisions already made
- `normalize_title()` lowercases before prefix stripping — fixes site title-case changes breaking matches
- `_resolve_url()` in `get_comic_download_url.py` skips HTTP check for `.cbz/.cbr` URLs (avoids timing out on large file downloads)
- Search cache (`/app/cache/search_cache.json`) stores `seen_urls` (ALL URLs encountered, not just filtered ones) so broad searches like "The Darkness" (97 pages) stop early on re-runs
- 2s delay + 30s backoff on 429 in `get_comic_download_url.py` to avoid rate limiting
- Metron is primary metadata source; ComicVine is automatic fallback — both return the same normalised dict

## Web Interface ✅ complete
Stack: FastAPI + SQLAlchemy + SQLite + Bootstrap 5 + HTMX + APScheduler

### Routes
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Redirect to `/series` |
| GET | `/series` | Series list (table, enable/disable/delete via HTMX) |
| GET | `/series/add` | Search Metron + select series |
| POST | `/series` | Create series |
| GET | `/series/{id}/edit` | Edit form |
| POST | `/series/{id}/update` | Update series |
| DELETE | `/series/{id}` | Delete (HTMX) |
| PATCH | `/series/{id}/toggle` | Enable/disable (HTMX) |
| GET | `/api/series` | JSON list of all series |
| GET | `/api/metron/search?name=` | HTMX partial — Metron search results with cover art |
| GET | `/api/metron/series/{id}/add-form` | HTMX partial — pre-filled add form |
| GET | `/api/verify-search` | HTMX partial — live getcomics.org page-1 check |
| GET | `/health` | `{"status": "ok"}` |

### Template structure
```
web/templates/
  base.html                        Bootstrap 5 + HTMX CDN, navbar, flash messages
  series_list.html                 Series table
  series_add.html                  Metron search page
  series_edit.html                 Edit form with verify button
  partials/
    series_row.html                <tr> reused by toggle route
    metron_results.html            Search result cards
    add_form.html                  Pre-filled add form with verify button
    verify_results.html            getcomics.org page-1 results
```

### Phases
- **Phase 1** ✅ — DB schema + SQLAlchemy models + migration from series_list.txt
- **Phase 2** ✅ — FastAPI skeleton + APScheduler replacing shell loop
- **Phase 3** ✅ — UI: series list, add/edit, Metron search-as-you-type
- **Phase 4** ✅ — Verify step: live getcomics.org check before saving
