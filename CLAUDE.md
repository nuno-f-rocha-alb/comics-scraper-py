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
- **Cache-first Metron:** passive page-opens never block on Metron. `api_series_issues` reads the issue cache
  with `refresh_if_stale=False` (serves stale cache; only fetches when the cache is empty, `block=False`).
  A nightly APScheduler cron job (`metron_nightly`, default `0 3 * * *` via `METRON_NIGHTLY_CRON`) refreshes
  meta + full issue lists (with titles, blocking) for **non-ended** series via
  `metron_refresh.run_refresh(force=True, skip_titles=False, only_active=True)`. Metron is otherwise only hit
  by explicit user actions (Add-Series search, "Fetch from Metron", manual refresh, reading-list search).
- **Download staging** (§6): comics are downloaded + cbr→cbz + tagged + final-named in a hidden
  `comics/.downloads` folder (`STAGING_SUBDIR`, `util.staging_dir()`), then moved into the library via
  dot-temp + `os.replace` (`util.install_to_library()`) so Komga never indexes a partial/untagged file.
  `process_downloaded_comic()` returns the final path; chown happens after the move. Worker wipes staging
  orphans on start. **Ops:** Komga ignores dotfolders, but verify `comics/.downloads` is excluded from the
  Komga library; if not, exclude it in Komga's library settings.

## Web Interface ✅ complete
Stack: FastAPI + SQLAlchemy + SQLite + Bootstrap 5 + HTMX + APScheduler

### Series model columns
`id, publisher, series_name, year, comicvine_volume_id, metron_series_id, annual_comicvine_volume_id, getcomics_search_name, cover_image_url, total_issues, enabled, created_at`
- `cover_image_url` / `total_issues` — populated from Metron on add or via the background refresh (POST `/api/metron/refresh`)
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
| POST | `/api/metron/refresh` | Kick background Metron refresh (tracked series: meta/covers/issues/pause). Returns immediately; SPA polls status. Replaces old sync-covers + cache/refresh |
| GET | `/api/metron/refresh/status` | Background-refresh status (running, progress, last_error, last_result) |
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

## Reading lists (§7) ✅ Phase A
Search Metron reading lists, add one → creates/finds the series and monitors **only** the issues the list
needs (filtered by `issue_type`: Core/Tie-In/Prologue/Epilogue), exports a **CBL** for Komga import, and can
**push directly to Komga** via its API.
- Tables: `ReadingList` + `ReadingListItem` (`web/models.py`) — these are the **local backup**: CBL, status
  and monitoring read from here, so Metron is hit only on add / re-sync (search uses a short in-memory TTL).
- Modules: `metadata/metron_reading_lists.py` (Metron `/reading_list/` client + `parse_item`),
  `web/cbl.py` (CBL XML — Komga matches on Series+Number, so Series = Metron name = what the app tags),
  `web/komga_client.py` (best-effort name+number → bookId match, create/update read list).
- Routes in `web/app.py`: `/api/reading-lists/search|/metron/{id}/preview`, `POST /api/reading-lists`,
  `GET /api/reading-lists[/{id}]`, `/resync`, `DELETE`, `/cbl`, `POST /push-komga`, `GET /api/komga/status`.
- Frontend: `ReadingLists.tsx` (search + add sheet) + `ReadingListDetail.tsx`.
- **Komga config is env-only** (`config.py` → `KOMGA_URL`, `KOMGA_API_KEY`; in `docker-compose.yml`).
  Unset → push route returns 400 and the UI hides the button. CBL export needs no config.
- **Nightly Komga re-push** (`scheduler._wrapped_komga_nightly`, `KOMGA_NIGHTLY_CRON` default `30 3 * * *`)
  create-or-updates every reading list on Komga so issues added since are picked up; no-op when unconfigured.
  Shares `web/app.py:_push_reading_list_komga` with the manual push route.
- **Phase B (deferred):** auto-suggest lists with ≥X% owned (needs a bounded background scan — Metron has no
  reverse "lists containing issue X" lookup). See `specs/reading-lists.md`.

## Backend tests (`tests/`) — run in a prod-faithful container
Backend deps (sqlalchemy/comicapi/…) aren't on the host, so tests run inside the same `python:3.12`
image as prod. `pytest` mocks every external boundary (DB → temp SQLite, comics → temp dir, worker
stubbed, Metron → `metron_get` fixture), so no network/library state is needed.
```powershell
docker build -f Dockerfile.test -t comics-test .            # once; rebuild only when requirements.txt changes
docker run --rm -v "${PWD}:/app" -w /app comics-test pytest -q
```
`pytest.ini` sets `pythonpath = .` so `import web.*` resolves. The harness + Dockerfile.test are
`.dockerignore`d, so they never bloat the prod image (the test image bind-mounts the repo at run time).
This is the real gate for backend changes — it ends the "edit blind, deploy, pray" loop.

## Frontend SPA (`frontend/`) — tooling gotchas
React SPA (Vite + React 19 + TS + Tailwind v4 + shadcn/ui), **the only UI** — served by FastAPI at `/`
in prod (root catch-all → index.html; `/api/*` + `/health` 404 as JSON). The legacy Jinja UI was retired
(MIGRATION-JOURNAL.md §15). See `MIGRATION-JOURNAL.md`.
- **Typecheck = `cd frontend && npm run build`** (runs `tsc -b` then Vite). Bare `tsc` misses the Vite step
  and the path-alias resolution — don't use it as the gate.
- **Backend deps are Docker-only on the host** (no sqlalchemy/comicapi locally), so `web/app.py` won't
  import and uvicorn won't run on the host. Verify the SPA against `frontend/mock_api.py` (stdlib,
  gitignored) via the `.claude/launch.json` "comics-frontend" preview (Vite 5173 → mock 8000). The backend
  itself (routes, the root StaticFiles mount) is tested via the Docker pytest harness above.
- **CodeRabbit free CLI caps at 150 files** → review per unit with `--type uncommitted`, not the whole branch.
- **lucide-react missing icons** hit during the migration: no `SlashCircle` (use `Ban`), no `ArrowClockwise`
  (use `RotateCw`).
- **`preview_screenshot` times out on fast-poll pages** (a 2–3s `refetchInterval` never lets the network go
  idle) — verify those via DOM `eval` checks instead.
