# cf-403-log-noise

Quiet the scheduled scraper's log for an expected Cloudflare-gate 403; keep real failures loud.

## Problem
`main.py:run_scraper` per-series `except` logs every failure at ERROR with `exc_info=True`. A
Cloudflare-gated mirror (e.g. `comicfiles.ru`) returns HTTP 403 at `download_file`'s
`raise_for_status()` — an expected, unactionable "skip and retry next run" condition — but it prints a
full traceback, looking like a crash.

## Requirements
- **R1** — In `run_scraper`'s per-series `except`, detect a 403: the exception is (or wraps) a
  `requests.exceptions.HTTPError` whose `.response.status_code == 403`. Log it at **WARNING**, one line,
  no traceback, naming Cloudflare and that it will retry next run. Include series name + publisher.
- **R2** — Any other exception keeps the current behaviour: ERROR + `exc_info=True` (full traceback).
- **R3** — Per-series resilience unchanged: the loop still continues to the next series after either case.
- Minimal: no new deps, no change to retry logic or the download path. Detect via
  `getattr(exc, "response", None)` + `status_code` so it works whether the HTTPError is raised directly
  or surfaces as `exc` (avoid over-narrow isinstance that misses wrapped cases; a non-403 response falls
  through to ERROR).

## Definition of done (objective gate)
1. `docker run --rm -v "${PWD}:/app" -w /app comics-test pytest -q` — full suite green + new test.
2. New test proves: a raised `HTTPError` with a 403 response → WARNING (no traceback), a generic
   `Exception` → ERROR with `exc_info`, and the loop continues to the next series in both cases. Assert via
   `caplog` levels/messages and that all series were attempted.
3. CodeRabbit `-t uncommitted` clean on the diff (no new Critical/Major).

## Out of scope
- The worker path (already hardened), retry/backoff, actually bypassing Cloudflare.
