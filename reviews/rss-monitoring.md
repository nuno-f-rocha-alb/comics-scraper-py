# review: rss-monitoring

## Spec requirements
- **R1 DownloadJob.url** — MET. Column + idempotent migration.
- **R2 worker uses post URL** — MET. `_download_issue(..., post_url=)` skips search when set; search
  extracted to `_search_for_issue`. Tested: post_url path does not call search.
- **R3 rss_monitor.poll_feed_and_enqueue** — MET. Reuses `_match_feed_entries`; dedup via local file +
  any existing DownloadJob (no new table); enqueues `source="rss"` jobs with the feed URL.
- **R4 auto-download safety** — MET. enabled (via `_match_feed_entries`) + selective MonitoredIssue +
  issue_min; normalized comparisons both sides.
- **R5 scheduled poll** — MET. Second APScheduler job `rss_poll` every `RSS_POLL_MINUTES` (default 30,
  parse-guarded), independent of the scraper; `rss_next_run_at` in status.
- **R6 manual Releases uses feed URL** — MET. Endpoint optional `url` (SSRF-allowlisted to getcomics.org);
  api.ts + Releases.tsx pass `m.url`.

## Gate
- **Docker pytest**: PASS — 41 passed (was 32; +9 in `tests/test_rss_monitor.py`: enqueue, skip on local
  file / any existing job (no retry-storm) / selective monitoring / issue_min / disabled / non-getcomics
  URL; worker post_url skips search; endpoint rejects foreign URL).
- **Frontend build**: PASS — `npm run build` clean.
- **CodeRabbit**: 14 → 1 over two passes.
  - Fixed (mine): **critical SSRF** (client `url` → server fetch) via getcomics allowlist + feed-URL guard;
    worker decimal filename parity with the scraper; scheduler env-parse guard; normalized both sides of
    dedup/monitor checks.
  - Skipped w/ reason: commit-before-enqueue (reversing introduces a worse worker race; queue.put doesn't
    fail; worker re-enqueues stuck jobs on restart). Spec decimal note (deliberate parity with the existing
    truncating layer).
  - **Deferred (backlog, affects whole codebase equally):** worker search-match vs filename decimal
    normalization = the §2 cross-layer `str(int(float(n)))` issue needing a coordinated change + stored-key
    migration. Pre-existing findings in untouched files (api_rename_apply series-scoping, `_find_issue_file`
    / `_extract_nums` decimals, scanner.py import-in-try, SeriesAdd/Edit stale verify, stale
    `specs/series-list.md`) left to backlog.
- **Live verify**: `poll_feed_and_enqueue` exercised against the real function (mocked feed + temp DB),
  asserting the actual DownloadJob rows + `worker.enqueue` calls.

## Result: PASS (1 finding deferred to the decimal-normalization backlog unit).
