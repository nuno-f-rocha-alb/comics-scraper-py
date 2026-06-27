# spec: metron-async-refresh (BACKEND-JOURNAL §4 — Metron overhaul)

## Objective
The two web "refresh from Metron" buttons run **synchronously on the request thread** and can
`time.sleep` 60s+ on a Metron burst limit (`metron_client.get(block=True)`), holding the HTTP request
open for minutes. `/api/metron/cache/refresh` additionally paginates the **entire Metron series catalog**
(thousands of series) on every click — pure pre-warm, since `_metron_search_json` already has a live-API
fallback. Move the refresh off the request thread, drop the full-catalog pagination, and skip redundant
per-series detail calls with a TTL.

## Established pattern to mirror (do NOT invent a new one)
`web/scanner.py` — module-level `_lock`/`_running`/`_last_*`/`_progress`, `get_status()`, `run_scan()`
spawns a daemon thread, returns `False` if already running. Exposed via `GET /api/library/status` +
`POST /api/library/scan` (`_scan_status_json()` serializes datetimes). Frontend `Library.tsx` polls
`refetchInterval: (q) => (q.state.data?.running ? 2000 : false)`. Replicate this shape exactly.

## Requirements

### R1 — `web/metron_refresh.py` (new, mirrors `web/scanner.py`)
- Module state under a `threading.Lock`: `_running`, `_last_refresh_at: datetime|None`,
  `_last_error: str|None`, `_progress: {"current","done","total"}`, `_last_result: dict` (counts:
  `refreshed`, `ids_found`, `skipped`, `errors`).
- `get_status() -> dict` — snapshot under lock.
- `run_refresh(force: bool) -> bool` — returns `False` if already running, else sets `_running=True` and
  spawns a `daemon=True` thread named `metron-refresh`. Worker:
  - opens its own `SessionLocal()`,
  - queries **tracked** series (all `Series`, so cv_id→metron_id discovery still runs),
  - per series: update progress, call `_refresh_one_series(s, db, force=force)` (R2), tally counts,
  - on `RateLimitedError`: record `_last_error`, `db.commit()`, stop the loop gracefully (don't crash),
  - `finally`: `db.commit()`, `_running=False`, `_last_refresh_at=now`.
  - **No top-level import of `web.app`** (circular) — import `_refresh_one_series` lazily inside the worker,
    exactly as `scanner.py` imports `retag_comics` inside `_worker`.
- Uses `block=True` Metron calls — sleeping on a rate limit is fine now that it's off the request thread.

### R2 — `_refresh_one_series(s, db, *, force) -> bool` in `web/app.py` (extract + unify)
Single per-series refresh path, replacing the duplicated logic in `sync_covers`, `metron_cache_refresh`'s
tracked-series tail, and `main._refresh_metron_caches`:
1. If `not s.metron_series_id and s.comicvine_volume_id`: look up metron id via
   `GET /series/?cv_id=...`, set `s.metron_series_id`, count as `ids_found`.
2. If `s.metron_series_id`:
   - **TTL gate (smarter refresh):** if `not force` and `s.metron_refreshed_at` is within
     `_METRON_META_TTL` (7 days), **skip** the detail call (count `skipped`) but still ensure the issue
     list is cached via `_get_or_fetch_metron_issues(..., force=False)` (respects its own 7-day TTL).
   - else: `_refresh_series_meta_from_metron(s, db)`; on success → cover fallback (first-issue cover when
     `not s.cover_image_url`), `_get_or_fetch_metron_issues(mid, db, force=force, skip_titles=True)` for
     regular + annual ids, `_recompute_pause_state(s, db)`, set `s.metron_refreshed_at = utcnow()`.
- Returns `True` if it refreshed (or found an id), `False` if skipped/nothing to do.
- Honors explicit user intent: manual button → `force=True` (always refresh); scheduler → `force=False`
  (TTL cuts redundant calls across runs).

### R3 — `Series.metron_refreshed_at` column
- Add `metron_refreshed_at: Mapped[datetime|None]` to `web/models.py:Series`.
- Add `ALTER TABLE series ADD COLUMN metron_refreshed_at TIMESTAMP` to `database.py:migrate_columns()`
  (same idempotent `if "..." not in cols` pattern). Safe for existing DBs.

### R4 — Endpoints (`web/app.py`)
- `GET /api/metron/refresh/status` → `_metron_refresh_status_json()` (serialize datetimes like
  `_scan_status_json`): `{running, last_refresh_at, last_error, progress, last_result}`.
- `POST /api/metron/refresh` → `started = _metron_refresh.run_refresh(force=True)`; return
  `{started, ...status}`.
- **Remove** `POST /api/metron/cache/refresh` (full-catalog pagination — dropped; search live-fallback
  covers it) and `POST /api/sync-covers` (folded into the background job). Grep first to confirm the SPA
  is the only caller (Jinja UI is retired). `_metron_search_json` is unchanged — its live-API fallback
  stays the search path.
- `main.py:_refresh_metron_caches` → loop calling `_refresh_one_series(s, db, force=False)` (keeps the
  RateLimitedError early-stop + `_recompute_pause_state`); no behavior regression for the scheduler.

### R5 — Frontend (`frontend/src/pages/SeriesList.tsx` + `lib/api.ts`)
- Replace the two header buttons ("Refresh Cache" + "Sync Covers") with **one** "Refresh from Metron"
  button + a compact status indicator (running spinner + `done/total`, else last-refresh time / error),
  mirroring `Library.tsx`'s status card pattern (reuse existing shadcn `Button` + lucide `RefreshCw`/
  `Loader2`; no new component lib).
- `lib/api.ts`: `getMetronRefreshStatus()` → `GET /api/metron/refresh/status`;
  `startMetronRefresh()` → `POST /api/metron/refresh`. Add a `MetronRefreshStatus` type.
- Poll status with `refetchInterval: (q) => (q.state.data?.running ? 2000 : false)`. On start success,
  `setQueryData` + `invalidateQueries` (same as Library), and `invalidateQueries(["series-overview"])`
  when a run finishes so covers/progress update.

## Out of scope (ponytail — do not build)
- ETag/conditional-GET on Metron (TTL is enough; Metron sends no usable validators here).
- Per-issue title backfill changes (`skip_titles` stays as-is).
- Any RSS work (separate journal unit).
- Keeping `MetronCache` full-catalog pre-warm — deliberately dropped.

## Definition of done (objective gate)
1. **Docker pytest** green (existing 20 + new):
   `docker build -f Dockerfile.test -t comics-test .` (only if requirements changed) then
   `docker run --rm -v "${PWD}:/app" -w /app comics-test pytest -q`.
   New `tests/test_metron_refresh.py` (Metron mocked via `metron_get` fixture, temp DB):
   - `_refresh_one_series`: fresh `metron_refreshed_at` + `force=False` → meta call **skipped**, timestamp
     unchanged; stale/None + `force=False` → refreshed, timestamp set; `force=True` always refreshes.
   - cv_id→metron_id discovery sets `metron_series_id` (counts `ids_found`).
   - `run_refresh` returns `False` when `_running` already set.
   - `GET /api/metron/refresh/status` returns the documented keys; `POST /api/metron/refresh` returns
     `{started: true, ...}` and the removed routes 404 (live-verify the wiring via TestClient).
2. **Frontend typecheck**: `cd frontend && npm run build` clean (tsc -b + vite).
3. **CodeRabbit** clean on changed files (code change — gated).
4. **Live verify**: TestClient hits `/api/metron/refresh` + `/status` against real functions (mocked
   Metron) and asserts real output — the pytest run is the runtime check. SPA button+status panel verified
   against `frontend/mock_api.py` preview (mock the two new routes).
