# spec: rss-monitoring (BACKEND-JOURNAL §5 — feed-driven monitoring)

## Objective
Add Sonarr-style feed-driven monitoring: a scheduled poll of getcomics.org's RSS feed auto-enqueues
downloads for new issues of monitored series — the **main** download path. The existing per-series
getcomics **search** scraper stays scheduled as the **back-catalog / older-issues net** (user decision).
Both feed the same download queue and must coexist without double-downloading.

## What already exists (reuse, don't rebuild)
- `comic_search/rss_feed.py` — `fetch_feed()` → `list[FeedEntry]` (title, url, parsed series/number/year).
  One HTTP request, ~10 newest posts.
- `web/app.py:_match_feed_entries(entries, db)` — matches feed entries to **enabled** series by normalized
  name, tags each `downloaded` (local file) + `queued` (in-flight job). Reuse verbatim.
- `web/app.py:_local_issue_numbers(s)` — local file issue set.
- Download worker (`web/worker.py`): `enqueue(job_id)` → `_process` → `_download_issue` which currently
  **re-searches** getcomics to resolve the post URL.
- `GET /api/releases` + `Releases.tsx` — manual Download buttons (kept).
- `DownloadJob(series_id, issue_number, search_term, status, source, filename, error, ...)` — `source` is
  already `manual`/`scraper`; add `rss`.

## Dedup design (no new table)
The poll re-evaluates only ~10 in-memory feed entries each tick (single feed fetch, no per-entry network),
so **no "seen entries" store is needed**. An issue is skipped when **a local file exists** OR **any
`DownloadJob` exists for (series_id, issue_number) in ANY status** (queued/downloading/done/failed/
cancelled). Reusing `DownloadJob` rows as the ledger means a *failed* download is NOT retried every poll
(no retry-storm); the user re-triggers via the existing manual Download / by clearing the failed job.

## Requirements

### R1 — `DownloadJob.url` column (precise RSS downloads)
- Add `url: Mapped[str | None]` to `web/models.py:DownloadJob`.
- `database.py:migrate_columns()` — idempotent `ALTER TABLE download_jobs ADD COLUMN url TEXT`.
- Purpose: RSS already knows the exact getcomics post URL — store it so the worker downloads directly
  instead of re-searching (lighter + avoids search mismatch). `None` → existing search behaviour.

### R2 — worker uses the post URL when present (`web/worker.py`)
- `_process` passes `post_url=job.url` into `_download_issue`.
- `_download_issue(series, issue_number, *, post_url=None, ...)`: when `post_url` is set, skip the search
  block entirely and use it as `comic_url`; otherwise keep the current precise+broad search. Everything
  after URL resolution (`get_comic_download_url` → `download_file` → `process_downloaded_comic`) unchanged.

### R3 — `comic_search/rss_monitor.py` (new) — the poll
- `poll_feed_and_enqueue() -> dict` (returns `{feed_size, enqueued, skipped}`):
  - `entries = fetch_feed()` (let exceptions propagate to the scheduler wrapper, which logs).
  - open `SessionLocal()`; `matches = _match_feed_entries(entries, db)` (lazy import from `web.app`, same
    pattern as `main.py`).
  - for each match: skip if `m["downloaded"]`; skip if `_has_existing_job(db, series_id, num)`; skip if not
    `_issue_is_monitored(db, series, num)` (R4). Else create
    `DownloadJob(series_id, issue_number=num, search_term=entry.title, url=entry.url, source="rss",
    status="queued")`, commit, `worker.enqueue(job.id)`.
  - log a one-line summary; return counts.
- `_has_existing_job(db, series_id, num)` — true if any `DownloadJob` for that series has a normalized
  `issue_number == num` (normalize both with the codebase's `str(int(float(n)))`, falling back to raw).
- Annuals are out of scope for the auto-poll (feed titles like "X Annual #1" parse as series "x annual",
  which won't match a base series — back-catalog search handles annuals). Regular issues only.

### R4 — auto-download safety filter (`_issue_is_monitored`)
Auto-download must respect the same gates the scraper honours (it downloads without a click):
- Series must be `enabled` — already guaranteed (`_match_feed_entries` filters `enabled == True`; covers
  auto-pause too).
- **Selective monitoring:** if the series has `MonitoredIssue` rows of type `regular`, only enqueue when
  `num` is in that set (numbers stored normalized). No rows → monitor-all (enqueue).
- **Lower bound:** skip if `num` (when integer) `< series.issue_min`.
- (No upper bound — RSS issues are new by definition; `issue_max` is unused per models.)

### R5 — scheduled RSS poll (`web/scheduler.py`)
- Add a second APScheduler job (`id="rss_poll"`, `IntervalTrigger(minutes=RSS_POLL_MINUTES)`,
  `RSS_POLL_MINUTES = int(os.getenv("RSS_POLL_MINUTES", "30"))`, `next_run_time=now`) calling a wrapper
  that runs `poll_feed_and_enqueue()` under try/except (log failures; never crash the scheduler).
- Independent of the existing `scraper` job, which stays exactly as-is (the back-catalog net).
- Surface the RSS job's `next_run_at` in `get_status()` (additive key `rss_next_run_at`); no new UI config
  (interval is env-configurable — UI config deferred, YAGNI).

### R6 — manual Releases download uses the feed URL (small fix)
- `POST /api/series/{id}/issues/{number}/download` gains optional `url: str | None = Query(default=None)`;
  when provided it's stored on the job so the worker downloads directly (same benefit as R2). Back-compat:
  omitted → search.
- `frontend/src/lib/api.ts:downloadIssue(seriesId, num, url?)` appends `?url=` when given.
- `Releases.tsx` passes `m.url` to `downloadIssue`. (SeriesDetail's issue-download caller unchanged → still
  searches.)

## Out of scope (ponytail)
- "Seen feed entries" table (DownloadJob + local files already dedup).
- Kill-switch / per-series auto-download toggle (user wants auto; disable via series.enabled / env).
- UI for RSS interval config (env var for now).
- Annual auto-monitoring via RSS.
- Replacing/retiring the search scraper (kept as the back-catalog net).
- `search_cache.json` changes.

## Definition of done (objective gate)
1. **Docker pytest** green (existing 32 + new `tests/test_rss_monitor.py`):
   `docker run --rm -v "${PWD}:/app" -w /app comics-test pytest -q`.
   New tests (feed + worker network mocked, temp DB):
   - poll enqueues a job for a new, monitored, not-downloaded, not-jobbed issue — with `url` set +
     `source="rss"`.
   - poll skips when a local file exists; skips when ANY job already exists (queued/done/**failed**) →
     no retry-storm.
   - poll respects selective monitoring (issue not in `MonitoredIssue` set → skip) and `issue_min`.
   - poll skips disabled series (enabled=False).
   - worker `_download_issue` with `post_url` set resolves the link directly and does NOT call the search
     `requests.get` (assert search is skipped).
2. **Frontend typecheck**: `cd frontend && npm run build` clean.
3. **CodeRabbit** clean on changed files — run via WSL Debian:
   `wsl -d Debian -e sh -lc "cd /mnt/c/Users/nunob/Repositorios/comics-scraper-py && coderabbit review --agent -t uncommitted"`
   (filter findings to changed files).
4. **Live verify**: `poll_feed_and_enqueue()` exercised in tests against the real function (mocked feed +
   temp DB) asserting the actual `DownloadJob` rows created + `worker.enqueue` calls = the runtime check.
   SPA Releases change is typecheck-verified (no mock_api/preview in this checkout).
