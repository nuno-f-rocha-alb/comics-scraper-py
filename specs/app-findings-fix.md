# app-findings-fix

Fix the CodeRabbit "major" findings in `web/app.py` that survive verification against current code.

## Findings triage (verified against current code)

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | Issue-number normalization collapses decimals (`#1.5`→`1`) and `_find_issue_file` crashes on non-numeric input | **VALID** | Fix |
| 2 | RSS `by_norm` map overwrites same-normalized series names → entry binds to wrong volume | **VALID** | Fix |
| 3 | `api_series_issues` returns JSON but "must stay HTMX partial" | **STALE** | Skip — Jinja/HTMX UI fully retired (no `web/templates/`), app is React SPA; JSON is the correct contract |
| 4 | Reading-list monitored insert hardcodes `issue_type="regular"` | **FALSE POSITIVE** | Skip — `MonitoredIssue.issue_type` is the regular/annual *folder* axis; reading-list `p["issue_type"]` is Core/Tie-In/Prologue/Epilogue (membership axis) and is never `"annual"`. Reading lists resolve to a regular `Series` volume, so `"regular"` is correct. CodeRabbit's suggested mapping would mis-store. |

## Requirements

### R1 — Shared decimal-safe issue normalization
- `util.norm_issue_number` (already exists) is the canonical helper: `"001"`==`"1"`==`"1.0"`→`"1"`, `"1.5"`→`"1.5"`, non-numeric/None → stripped raw string (never raises).
- Replace **every** `str(int(float(...)))` issue-number normalization in `web/app.py` with `norm_issue_number(...)`, so both sides of every comparison normalize identically. Sites (line numbers pre-edit): 66, 310, 327, 352, 972, 1048, 1303, 1419, 1460, 2001, and `_norm_issue_num` (2308-2313).
- Drop the now-redundant `try/except (ValueError, TypeError)` wrappers that existed only because `int(float())` raised — `norm_issue_number` already falls back to the raw string.
- `_norm_issue_num` → delegate to `norm_issue_number` (keep the name; it's used in the reading-list section) OR remove and inline. Smallest correct diff wins.
- Add a top-level `from util import norm_issue_number` import (currently imported locally inside `_match_feed_entries`); remove the now-redundant local import.
- **Behaviour invariant:** integer issue numbers must normalize byte-identically to before (no regression in existing matches); only decimals and non-numeric input change.

### R2 — RSS series matching must not silently pick the wrong volume
- In `_match_feed_entries`, `by_norm` currently maps `normalized_name -> Series`, overwriting on collision. Change it to map `normalized_name -> list[Series]`.
- When resolving a feed entry:
  - 0 candidates → skip (unchanged).
  - 1 candidate → use it.
  - >1 candidates → disambiguate by year: match the feed entry's `year` (FeedEntry.year, parsed from `Series #N (YYYY)`) against `Series.year`. If exactly one candidate matches, use it. If zero or more-than-one match (still ambiguous), **skip the entry** (do not fall back to an arbitrary volume).

## Definition of done (objective gate)
1. `docker run --rm -v "${PWD}:/app" -w /app comics-test pytest -q` — full suite green (81 tests + any new).
2. New/updated tests prove: `norm_issue_number` keeps `#1.5` distinct from `#1`; `_find_issue_file` returns `None` (no crash) on non-numeric issue_num; `_match_feed_entries` binds to the correct volume by year when two enabled same-name series exist, and skips when ambiguous.
3. CodeRabbit `-t uncommitted` on the diff: no new Critical/Major findings in the changed code.
4. Live-verify: import `web.app` inside the test container and call `_match_feed_entries` with a two-volume fixture (or a focused pytest) asserting correct binding.

## Out of scope
- Findings #3 and #4 (skipped with reasons above).
- Any refactor beyond routing normalization through the existing helper and the `by_norm` list change.
