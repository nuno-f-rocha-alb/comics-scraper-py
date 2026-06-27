# Backend Journal — comics-scraper-py

Incremental backend modernization after the SPA migration. Goal order (each
shippable on its own): **testability → bug fixes → Metron overhaul → RSS-feed
monitoring**. Hard constraint for the RSS phase: search-based download of
back-catalog / old issues (issues not currently in the feed) must keep working —
RSS is additive monitoring, not a replacement for "go fetch issue #N".

Not a rewrite. The existing code carries hard-won edge-case knowledge (decimal
issue regex, title normalization, Metron rate-limit backoff, ComicVine fallback,
path-traversal guards). We modernize in place, gated by the test harness below.

## §1 — Testability (the foundation)

The whole SPA migration ran "edit blind → deploy → pray": backend deps
(sqlalchemy/comicapi) aren't installed on the host, so `web/app.py` couldn't even
import locally. Verification was `ast.parse` + `pyflakes` + stdlib replicas. That
ends here.

**Harness:** Docker is available on the host (29.5.2). Tests run inside the same
`python:3.12` base as prod (`Dockerfile.test`, deps baked into a cached layer;
the repo is bind-mounted at run time so code/test edits need no rebuild). Every
external boundary is faked in `tests/conftest.py`:
- DB → throwaway SQLite under a temp dir (`DB_PATH` env, set before any `web.*` import)
- comics library → temp dir (`COMICS_BASE_DIR`)
- download worker → stubbed (lifespan doesn't spawn threads)
- Metron → `metron_get` fixture monkeypatches `metadata.metron_client.get` (no network)

`pytest.ini` sets `pythonpath = .`. The harness is `.dockerignore`d so it never
ships in the prod image.

Run:
```
docker build -f Dockerfile.test -t comics-test .
docker run --rm -v "$PWD":/app -w /app comics-test pytest -q
```

**First suite (13 tests, all green in the container):**
- `test_smoke` — app imports, `/health`, empty overview, unmatched `/api/*` → 404 (the catch-all guard).
- `test_overview` — the full status classifier via `GET /api/series/overview`: continuing-complete,
  ended-complete, missing-monitored, missing-unmonitored, future-only-gap (not "behind"), active-download
  wins, and footer stats. This is the DB + slow-filesystem logic that was previously untestable.
- `test_scan` — `_scan_series_dir` (the perf fix): one listdir yields count + issue-number set, asserted
  equal to the old two-helper path; decimals/non-cbz handled; missing folder → `(0, set())`.

**Bonus:** the harness was bundled on the same branch as the `perf(series): single filesystem scan`
fix (`a13a8f5`) and the overview/scan tests validate it directly.

**Backlog surfaced by CodeRabbit while standing this up (deferred to §2 bugs, now fixable test-first):**
- `web/app.py _find_issue_file` — `str(int(float(issue_num)))` collapses `1.5 → 1` for both target and
  filename match → decimal issues collide in file lookup (delete/metadata/download). Same class as the
  §1 (migration) critical, in the lookup path.
- `downloader/download_file.py` — streaming response never explicitly closed (socket leak); suffix/extension
  handling. In the manual-download path.
- `util.py convert_cbr_to_cbz` — still deletes the source CBR on partial conversion.
- `downloader/test_issue_format.py` — a `__main__` assert script that re-implements the format logic;
  fold into the pytest suite against the *shipped* function.
- Repo hygiene: `.idea/` (esp. `workspace.xml`) is tracked, `.vscode/launch.json` has a machine path,
  `frontend/.vite/` is loose — gitignore + untrack.
- Two CodeRabbit "critical" duplicate-export claims on `frontend/src/lib/api.ts` were **false positives**
  (no duplicates; `npm run build` clean) — same stale-knowledge pattern seen in the migration.

## §2 — Bug fixes (test-first; staged on `fix/backend-bugs`, NOT deployed)

Done overnight, each gated by the Docker suite (17 green):
- **`download_file.py` socket leak** — the streaming `requests.get` was never closed on early exit
  (cancel/IOError). Wrapped in `with ... as response:` so the connection is released on every path.
  Tests (`test_download_file.py`, network mocked): response closed on success AND on mid-download cancel;
  `.part` scratch cleaned.
- **`util.py convert_cbr_to_cbz` partial-conversion data loss** — it deleted the source CBR whenever
  `written > 0`, so a partial conversion (some entries failed) lost the failed pages. Now deletes only on
  `written > 0 and failed == 0`. Tests (`test_convert_cbr.py`, rarfile mocked): clean → source removed,
  partial → source kept.
- **Repo hygiene** — untracked `.idea/` (already in `.gitignore`; files predated the rule); gitignored
  `frontend/.vite/`. Left `.vscode/launch.json` (possibly an intentional shared config — but it has a
  machine-specific path, see backlog).

**Deliberately NOT done unattended (need design or a wider, caller-touching change — for an awake session):**
- **`_find_issue_file` decimal collision** — the whole matching layer (`_extract_nums`, monitor-all,
  status) normalizes issue numbers with lossy `str(int(float(n)))` (`1.5 → 1`). Fixing one function would
  make decimal handling *more* inconsistent. Decision needed: does the app support decimal issues in
  matching at all? If yes, it's a coordinated change across the matching layer + a migration of stored keys.
- **`util.py` return contract** — still returns `cbz_path` even on partial/failed conversion, so callers
  treat a partial CBZ as success. Fixing means returning `None`/raising + updating every caller.
- CodeRabbit backlog (pre-existing, unverified — triage when awake): Metron lookup persists *failed*
  fetches into cache (`web/app.py` ~436); delete handlers don't refresh cached state (~1489);
  `_cleanup_old_logs` active-log path comparison (~1836); `retag_comics._issue_number` decimal normalize;
  `search_comics` year comparison; a few SPA hydration-guard nits (MetadataSheet/SeriesNotes/SeriesAdd);
  `.vscode/launch.json` machine path; stale `decisions.md`/`specs/series-list.md`.
