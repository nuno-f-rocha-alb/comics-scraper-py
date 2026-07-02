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

## §3 — Metron: don't cache failed fetches (test-first; `fix/metron-cache`)

**Bug (CodeRabbit ~436):** `_ensure_cover_cached` wrapped the Metron fetch in `try/except: pass`, then
*unconditionally* wrote `image_url=""` — the "tried, genuinely nothing" sentinel. So a transient failure
(network blip / rate limit / timeout) permanently poisoned the cache: the short-circuit at the top
(`cached.image_url is not None`) meant the cover would never be retried.

**Fix:** track `ok` (primary `/series/{id}/` fetch succeeded). On failure → `return None` without writing,
so it stays retryable. The cover-fallback issue call is now best-effort (its own try/except) so a fallback
failure doesn't discard the series fields we already got. `image_url=""` is still cached on a *successful*
fetch that genuinely has no image (so we don't refetch every page load). Tests (`test_metron_cache.py`,
Metron mocked): failed fetch → not cached / retryable; success → cached; success-but-no-image → `""` sentinel.

**Audited the sibling fetchers — both clean, no fix needed:**
- `_refresh_series_meta_from_metron` returns `False` on exception, persists nothing.
- `_get_or_fetch_metron_issues` / `_fetch_metron_issues` — failures *raise* (never return `[]`), so the
  stale-issue removal only fires on a genuinely empty series, not a transient failure.

20 tests green in the Docker harness.

**Still open for the heavier Metron work (recommend a fresh session — structural, not bugs):** the
"refresh all" path paginates every series synchronously (slow, blocks the request); fixed 2s inter-call
delays; cold-cache first-load latency. Candidates: background/async refresh, batching, smarter TTL.

## §4 — Metron overhaul: async refresh + unified path + smarter TTL (`flow/metron-async-refresh`)

The two web "refresh from Metron" buttons ran **synchronously on the request thread**; a Metron burst
limit (`metron_client.get(block=True)`) could `time.sleep` 60s+, holding the HTTP request open for
minutes. `/api/metron/cache/refresh` additionally paginated the **entire Metron series catalog**
(thousands of series) every click — pure pre-warm, since `_metron_search_json` already falls back to a
live `/series/?name=` API call. Built via `/flow` (spec → build → objective gate); spec in
`specs/metron-async-refresh.md`, gate record in `reviews/metron-async-refresh.md`.

**Shipped:**
- **`web/metron_refresh.py`** — background daemon module mirroring `web/scanner.py` (`get_status()` +
  `run_refresh(force)`, already-running guard, polled status). Refresh now runs off the request thread;
  `block=True` calls may sleep on a rate limit harmlessly. Per-series `commit`/`rollback` so one failure
  can't discard earlier series; lazy `web.app` import inside the guarded `try` (no import cycle, and an
  import failure still clears `_running`).
- **`_refresh_one_series(s, db, *, force)`** in `web/app.py` — single per-series path replacing the
  duplicated logic in the old `sync_covers`, `metron_cache_refresh` tail, and `main._refresh_metron_caches`:
  cv_id→metron_id discovery → **TTL-gated** detail refresh → first-issue cover fallback → issue-list
  cache → pause recompute. `RateLimitedError` propagates so the worker stops instead of hammering.
- **Smarter TTL** (`Series.metron_refreshed_at`, +`migrate_columns()` ALTER): the slow-changing **detail**
  call (cover/status/total_issues) is skipped when refreshed within 7 days and not forced. Manual button →
  `force=True` (always). Scheduler → `force=False` (TTL cuts redundant detail calls). **Crucially the
  issue-list refresh ALWAYS runs (`force=True`) and pause is ALWAYS recomputed**, even on the TTL-skip
  path — preserving the §`_refresh_metron_caches` guarantee that new Metron issues stay visible to the
  scraper, and that completed series still auto-pause.
- **Full-catalog pre-warm dropped** — `/api/metron/cache/refresh` + `/api/sync-covers` removed; search
  relies on its existing live-API fallback. New endpoints: `POST /api/metron/refresh` (kick) +
  `GET /api/metron/refresh/status` (poll). SPA: two header buttons → one "Refresh from Metron" + spinner/
  progress (polls `running ? 2000 : false`, same pattern as the Library page).

**Gate:** Docker pytest 32 green (was 20; +12 in `tests/test_metron_refresh.py` — force/TTL-skip/stale/
cv-id/noop/rate-limit-propagation, run-guard, endpoint wiring, removed-route 404/405). `npm run build`
clean. CodeRabbit clean on changed files (5 majors fixed: RateLimitedError swallow, TTL-skip missing
pause recompute, scheduler hiding new issues, dirty-session rollback, `_running` stuck on import failure;
deferred findings were all in untouched pre-existing files — `retag_comics`/`config.py`/`utils.ts`/
`Downloads.tsx`/`test_issue_format.py`).

**Net −19 lines** despite the new module (two synchronous endpoints deleted).

**Root causes worth remembering:** (1) "smarter TTL" almost regressed new-issue visibility — the detail
call and the issue-list call have *different* freshness needs; only the detail call is TTL-safe. (2) A
single ORM session shared across a loop must commit/rollback per item, not once at the end, or a late
failure loses everything. (3) Broad `except Exception` in a helper on a rate-limit-sensitive path
silently defeats the `block=False`/`RateLimitedError` stop signal.

## §5 — RSS feed-driven monitoring (`flow/rss-monitoring`)

Sonarr-style monitoring: a scheduled RSS poll auto-enqueues downloads for new issues of monitored series
(the **main** path). The per-series getcomics **search** scraper stays scheduled as the **back-catalog
net** (user decision — both run in parallel, same download queue, no double-download). Built via `/flow`;
spec `specs/rss-monitoring.md`, gate `reviews/rss-monitoring.md`.

Much of the plumbing already existed (`comic_search/rss_feed.py`, `_match_feed_entries`, `/api/releases`,
`Releases.tsx` with manual Download). This unit added the **automation**:
- **`comic_search/rss_monitor.py:poll_feed_and_enqueue()`** — reuses `_match_feed_entries`, auto-enqueues
  `source="rss"` jobs carrying the feed post URL. **Dedup needs no new table:** skip if a local file
  exists OR any `DownloadJob` already covers (series, issue) in any status — so a *failed* download is not
  retried every poll (no retry-storm). Re-evaluating ~10 in-memory feed entries per tick is free.
- **Auto-download safety gate** — respects `enabled`/auto-pause, selective `MonitoredIssue` sets, and
  `issue_min` (normalized comparisons), so auto-download honours the same rules as the scraper.
- **`web/worker.py`** — `_download_issue(..., post_url=)`: when a job has a URL (RSS / Releases), download
  it **directly** instead of re-searching getcomics; the search path is extracted to `_search_for_issue`.
- **`web/scheduler.py`** — second APScheduler job `rss_poll` every `RSS_POLL_MINUTES` (default 30,
  parse-guarded), independent of the `scraper` job; `rss_next_run_at` in status.
- **`DownloadJob.url`** column + migration; the manual Releases "Download" now passes the feed URL too
  (was re-searching despite already knowing the URL).

**Security:** the worker fetches `job.url` server-side, so the download endpoint **allowlists
getcomics.org** (SSRF guard, `util.is_getcomics_url`); the poll also drops non-getcomics feed URLs.

**Gate:** 41 pytest (was 32; +9), `npm run build` clean, CodeRabbit 14→1. Fixed the critical SSRF I
introduced + worker decimal-filename parity + scheduler env-parse guard + normalized dedup. The lone
remaining finding is the **§2 cross-layer decimal-normalization backlog** (`str(int(float(n)))` truncates
`#1.5`→`1` across matching/overview/RSS) — deferred: it needs a coordinated change + stored-key migration,
not a piecemeal RSS edit.

**Backlog surfaced by CodeRabbit (pre-existing, untouched — for a future unit):** `api_rename_apply` isn't
scoped to the series_id's folder (contained within `COMICS_BASE_DIR`, so not arbitrary FS — but should be
series-scoped); `_find_issue_file`/`_extract_nums` decimal truncation (the §2 item); `web/scanner.py` lazy
import outside its try/finally (same class fixed in `metron_refresh` in §4); SeriesAdd/Edit leave stale
verify results on a failed retry; stale `specs/series-list.md`.

**Next:** download-staging (spec'd in `specs/download-staging.md` — stage downloads in
`COMICS_BASE_DIR/.downloads`, tag+convert+rename there, atomic-move the finished `.cbz` to the Komga
library) and the decimal-normalization backlog unit.

## §6 — Decimal-safe normalization + RSS volume matching + CF download guard (`flow/app-findings-fix`)

Closes the **§2 cross-layer decimal-normalization backlog** flagged since §5. `util.norm_issue_number`
already existed (decimal-safe, non-raising); the fix was to actually *use* it. Routed all 11
`str(int(float(n)))` sites in `web/app.py` (plus the worker's search-match `target_num`) through it, so
`#1.5` no longer collapses onto `#1` and `_find_issue_file` no longer 500s on non-numeric route input.
Integer normalization is byte-identical to before — no stored-key migration needed after all (the feared
migration was avoidable because integers normalize the same both ways; only decimals/non-numeric change).

**RSS volume bug:** `_match_feed_entries` indexed `by_norm[name] = Series`, silently overwriting when two
enabled volumes share a title (two "Teen Titans" runs) — an entry could bind to the wrong volume. Now
`by_norm[name] = list[Series]`; single → use it, multiple → disambiguate by the feed's parsed year, still
ambiguous → skip (never guess).

**Also:** `_build_issue_list` hardens explicit-null issue number (`None`→`""` not `"None"`); the
`convert_cbr_to_cbz` "Not a RAR file" confusion is separately fixed in `downloader/download_file.py` by
sniffing magic bytes right after download (a Cloudflare challenge page saved as `.cbr` now fails fast with
a clear "possibly blocked" error instead of a cryptic RAR error downstream).

**Findings triage (CodeRabbit + the original 4):** fixed the 2 in-scope (app decimal, worker decimal);
verified 2 **stale/false-positive** and skipped with reasons — `api_series_issues` "must be HTMX partial"
(Jinja UI retired, no `web/templates/`, SPA consumes JSON) and reading-list `issue_type="regular"`
(`MonitoredIssue.issue_type` is the regular/annual *folder* axis, not the Core/Tie-In membership axis, so
the constant is correct). Built via `/flow`; spec `specs/app-findings-fix.md`, gate
`reviews/app-findings-fix.md`.

**Gate:** 87 pytest (was 81; +6 — decimal distinctness, non-numeric safety, RSS year-disambiguation),
CodeRabbit clean on the diff. Updated `test_scan.py` which had *encoded the `#1.5`→`1` bug as expected
behaviour*.

**Backlog surfaced (pre-existing, deferred to tasks):** Metron issue-cache sync (`web/app.py` ~442-531)
prunes all stale rows on an empty/transient fetch → wipes a series' cache (data-loss guard needed —
**fixed in §7 below**); `_wrapped_metron_nightly` (`web/scheduler.py`) may not thread `only_active=True`
per CLAUDE.md's documented intent; `downloader/test_issue_format.py` duplicates prod format logic instead
of importing it.

**Next:** nothing queued. (The §5 "Next: download-staging" is done — shipped in
`util.staging_dir()`/`install_to_library()` + `web/worker.py`, see CLAUDE.md §6. Small deferred
backlog remains: `_wrapped_metron_nightly` `only_active` thread-through, `downloader/test_issue_format.py`
importing prod `format_issue`.)

## §7 — Guard Metron issue-cache prune against empty fetch (`flow/metron-cache-prune-guard`)

Closes the data-loss guard flagged in §6's backlog. `_get_or_fetch_metron_issues` computed
`stale_ids = existing - seen` and deleted them unconditionally. On an empty/partial Metron response
(transient API hiccup, rate-limit returning `{}`), `seen_ids` is empty → *every* cached row is "stale" →
the whole series' issue cache gets wiped. One-line guard: `if stale_ids and seen_ids:` — treat "fetched
nothing but had a cache" as transient and skip the prune (same rationale as the adjacent `new_total` guard
that already protects `Series.total_issues`).

**Gate:** 88 pytest (+1 — `test_empty_fetch_does_not_wipe_cache`: seeds a cache, forces a fetch returning
`[]`, asserts rows survive), CodeRabbit clean (0 findings).

**Next:** nothing queued. (The §5 "Next: download-staging" is done — shipped in
`util.staging_dir()`/`install_to_library()` + `web/worker.py`, see CLAUDE.md §6. Small deferred
backlog remains: `_wrapped_metron_nightly` `only_active` thread-through, `downloader/test_issue_format.py`
importing prod `format_issue`.)

## §8 — Quiet the scheduled scraper's Cloudflare-gate 403 log (`flow/cf-403-log-noise`)

The scheduled scraper (`main.py:run_scraper`, distinct from the worker path) logged **every** per-series
failure at ERROR with `exc_info=True`. A Cloudflare-gated mirror (`comicfiles.ru`) returns HTTP 403 at
`download_file`'s `raise_for_status()` — expected and unactionable — so a routine "mirror blocked, retry
next run" printed a full traceback that reads like a crash. New `_is_http_403(exc)` duck-types on
`exc.response.status_code` (no `requests` import; matches wrapped/re-raised HTTPErrors too); a 403 logs a
one-line WARNING naming Cloudflare, everything else keeps ERROR + traceback. Per-series resilience
unchanged. This is the scraper-side half of the turn-1 "distinguish CF-blocked from real errors" ask (the
worker/magic-byte guard was §6); note the 403 raises *before* any bytes, so the magic-byte sniff never
applied to it.

**Gate:** 90 pytest (+2 — `run_scraper` logs 403→WARNING/no-traceback, generic→ERROR/exc_info, loop
continues; `_is_http_403` unit), CodeRabbit clean (0 findings).

**Next:** nothing queued (same small deferred backlog as §7).

## §9 — Actually bypass the Cloudflare "are you human" challenge (`flow/cf-challenge-solver`)

§6/§8 only *detected* a CF gate (magic-byte guard) or *quieted* its 403 log; this §9 gets through it. New
`downloader/cf_solver.py` — a thin **FlareSolverr** client (headless-browser solver, the *arr-stack
standard) plus a per-host clearance cache. Chose a compose **sidecar over in-tree puppeteer/Playwright**:
this is a Python single-container app with no Node runtime, and a solver can't stream 50 MB files anyway —
so it solves only the *HTML* and hands back CF clearance (cookies + the exact UA they're bound to) that the
existing `requests` download replays. Enabled iff `FLARESOLVERR_URL` is set → every helper is a transparent
no-op otherwise, zero behaviour change when off. `get_comic_download_url` fetches the page via the solver
(falls back to plain `requests` + the 429 back-off when disabled); `download_file` calls `clearance_for(url)`
and merges the host's cookies + UA before the stream, so a **gated mirror** (different CF zone than
getcomics.org) is solved on its own host. Optional single `PROXY_URL` threads into the FlareSolverr payload
only when set — no rotation (ponytail ceiling; add per-request rotation if IP bans start). Clearance cache
is process-lifetime only; a worker restart just re-solves. `docker-compose.yml` gains the `flaresolverr`
service + `depends_on`.

Key correctness note: CF `cf_clearance` is host-scoped **and** UA-bound, so `download_file` must override
`User-Agent` with the solver's UA (not our static `HEADERS` UA) or the replayed cookie is rejected.

**Gate:** 96 pytest (+7 — `test_cf_solver.py`: disabled no-op/no-POST, /v1 payload incl. proxy-iff-set,
solution parse (HTML/cookies/UA), per-host cache skips a 2nd POST, POST-raises→None-one-WARNING,
non-ok-status→None), CodeRabbit clean (0 findings, code + compose infra).

**Next:** nothing queued. Deferred: route getcomics **search** (`comic_search/`) through `cf_solver` if it
starts getting gated; proxy rotation if a single egress IP gets banned.
