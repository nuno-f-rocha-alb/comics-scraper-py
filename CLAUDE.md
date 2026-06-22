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

### Series model columns
`id, publisher, series_name, year, comicvine_volume_id, metron_series_id, annual_comicvine_volume_id, getcomics_search_name, cover_image_url, total_issues, enabled, created_at`
- `cover_image_url` / `total_issues` — populated from Metron on add or via POST `/api/sync-covers`
- New columns added via `migrate_columns()` in `database.py` (ALTER TABLE — safe for existing DBs)

### Routes
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Redirect to `/series` |
| GET | `/series` | Card grid of series with cover art + progress bars |
| GET | `/series/add` | Search Metron + select series |
| POST | `/series` | Create series |
| GET | `/series/{id}` | Detail page: cover, progress, issues table |
| GET | `/series/{id}/edit` | Edit form |
| POST | `/series/{id}/update` | Update series |
| POST | `/series/{id}/toggle` | Enable/disable (form POST, redirects to detail) |
| PATCH | `/series/{id}/toggle` | Enable/disable (HTMX, returns series_row.html partial) |
| DELETE | `/series/{id}` | Delete (HTMX) |
| GET | `/series/{id}/issues` | HTMX partial — issues table from Metron vs local files |
| GET | `/api/series` | JSON list of all series |
| POST | `/api/sync-covers` | Fetch covers + issue counts from Metron for series missing them |
| GET | `/api/metron/search?name=` | HTMX partial — Metron search results with cover art |
| GET | `/api/metron/series/{id}/add-form` | HTMX partial — pre-filled add form |
| GET | `/api/verify-search` | HTMX partial — live getcomics.org page-1 check |
| GET | `/health` | `{"status": "ok"}` |

### Issue status logic (detail page)
Local files scanned from `/app/comics/{publisher}/{series_name} ({year})/` for `.cbz`/`.cbr`.
Issue numbers extracted via regex `#(\d+(?:\.\d+)?)`, normalised to `str(int(float(n)))`.
Status: **Downloaded** (local match) → **Upcoming** (future date) → **Missing** (past date) → **TBA** (no date).

### Template structure
```
web/templates/
  base.html                        Bootstrap 5 + HTMX CDN, sidebar, card grid CSS
  series_list.html                 *arr-style card grid with cover art
  series_add.html                  Metron search page
  series_detail.html               Detail page: header + HTMX issues table
  series_edit.html                 Edit form with verify button
  partials/
    series_row.html                <tr> reused by PATCH toggle route
    series_issues.html             Issues table (HTMX partial)
    metron_results.html            Search result cards
    add_form.html                  Pre-filled add form (includes hidden cover_image_url + total_issues)
    verify_results.html            getcomics.org page-1 results
```

### Phases
- **Phase 1** ✅ — DB schema + SQLAlchemy models + migration from series_list.txt
- **Phase 2** ✅ — FastAPI skeleton + APScheduler replacing shell loop
- **Phase 3** ✅ — UI: series list, add/edit, Metron search-as-you-type
- **Phase 4** ✅ — Verify step: live getcomics.org check before saving
- **Phase 5** ✅ — *arr-style card grid + series detail page with issues list

## Frontend SPA (`frontend/`) — tooling gotchas
React SPA (Vite + React 19 + TS + Tailwind v4 + shadcn/ui) migrated from the Jinja UI; served by FastAPI
under `/app` in prod (legacy Jinja pages kept at `/` until parity sign-off). See `MIGRATION-JOURNAL.md`.
- **Typecheck = `cd frontend && npm run build`** (runs `tsc -b` then Vite). Bare `tsc` misses the Vite step
  and the path-alias resolution — don't use it as the gate.
- **Backend deps are Docker-only on the host** (no sqlalchemy/comicapi locally), so `web/app.py` won't
  import and uvicorn won't run on the host. Verify the SPA against `frontend/mock_api.py` (stdlib,
  gitignored) via the `.claude/launch.json` "comics-frontend" preview (Vite 5173 → mock 8000). The `/app`
  StaticFiles mount itself is only testable in Docker.
- **CodeRabbit free CLI caps at 150 files** → review per unit with `--type uncommitted`, not the whole branch.
- **lucide-react missing icons** hit during the migration: no `SlashCircle` (use `Ban`), no `ArrowClockwise`
  (use `RotateCw`).
- **`preview_screenshot` times out on fast-poll pages** (a 2–3s `refetchInterval` never lets the network go
  idle) — verify those via DOM `eval` checks instead.
