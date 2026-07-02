# Review — app-findings-fix

## Requirements
- **R1 (decimal-safe normalization)** — MET. All 11 `str(int(float(...)))` sites in `web/app.py`
  routed through `util.norm_issue_number`; redundant try/except dropped; `_norm_issue_num` delegates;
  top-level import added, local import trimmed. Integer normalization byte-identical to before.
- **R2 (RSS volume disambiguation)** — MET. `by_norm` now `dict[str, list[Series]]`; single candidate
  used directly, multiple disambiguated by feed year, ambiguous → skip.

## Gate
1. `pytest -q` (Docker) — **87 passed** (81 prior + 6 new). `test_scan` updated (it encoded the
   old `#1.5→1` collapse bug as expected; now asserts `1.5` preserved).
2. Live-verify — new tests exercise real temp-DB Series + real filesystem comic files through
   `_find_issue_file` / `_local_issue_numbers` / `_match_feed_entries` and assert real output.
3. CodeRabbit `-t uncommitted` — see below.

## Skipped findings (verified stale / false-positive, not defects)
- **#3 api_series_issues HTMX**: no `web/templates/` exists; Jinja UI retired (SPA-only). JSON correct.
- **#4 reading-list issue_type**: `MonitoredIssue.issue_type` is regular/annual folder axis; reading-list
  `issue_type` is Core/Tie-In/Prologue/Epilogue and never "annual". Hardcoded "regular" is correct.

## Result: PASS (pending CodeRabbit clean)
