# review: metron-async-refresh

## Spec requirements
- **R1 metron_refresh.py** — MET. New daemon-thread module mirrors `web/scanner.py`; `get_status()` +
  `run_refresh(force)`; already-running guard; lazy `web.app` import (no cycle — confirmed, app imports
  clean in tests). `block=True` background calls.
- **R2 `_refresh_one_series`** — MET. Unified path: cv_id→metron_id discovery, TTL gate (skip detail when
  fresh & not force; still warms issue cache), meta refresh, first-issue cover fallback, issue cache,
  pause recompute, stamps `metron_refreshed_at`. RateLimitedError propagates. Replaces the duplicated
  logic in the old `sync_covers` / `metron_cache_refresh` / `_refresh_metron_caches`.
- **R3 column** — MET. `Series.metron_refreshed_at` + idempotent `ALTER TABLE` in `migrate_columns()`.
- **R4 endpoints** — MET. `GET /api/metron/refresh/status`, `POST /api/metron/refresh`; old
  `/api/metron/cache/refresh` + `/api/sync-covers` removed (now 405 — path only matches the GET catch-all);
  `main._refresh_metron_caches` routes through `_refresh_one_series(force=False)`.
- **R5 frontend** — MET. Single "Refresh from Metron" button + spinner/progress + last-error indicator;
  `getMetronRefreshStatus`/`startMetronRefresh` + type; polls `running ? 2000 : false`; invalidates
  series-overview on run completion.

## Gate
- **Docker pytest**: PASS — 31 passed (was 20; +11 in `tests/test_metron_refresh.py`). Real
  `_refresh_one_series` exercised for force/TTL-skip/stale/cv-id/noop; endpoint wiring + removed-route 405
  via TestClient.
- **Frontend typecheck**: PASS — `cd frontend && npm run build` clean (tsc -b + vite; pre-existing
  chunk-size warning only).
- **CodeRabbit**: NOT RUN — CLI not installed/on PATH in this session. **Gate incomplete** → not committed.
- **Live verify**: backend via TestClient against real functions (mocked Metron) = PASS. SPA browser
  render NOT run — no `mock_api.py`/`launch.json` preview in this checkout and backend can't run on host;
  change is a 1:1 mirror of the working Library page and passes typecheck.

## Result: HOLD — blocked on the CodeRabbit gate (and optional SPA live-render). Code green otherwise.
