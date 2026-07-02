# cf-challenge-solver

Get through Cloudflare's "are you human" / managed challenge on getcomics.org (and gated mirrors) by
routing HTML fetches through a **FlareSolverr** sidecar (headless-browser solver), then reusing the
returned CF clearance (cookies + user-agent) on the existing `requests`-based file download.

## Why FlareSolverr (not in-app puppeteer)
This is a Python + single-container app with no Node runtime. FlareSolverr is a drop-in Docker service
that solves CF challenges with a headless browser and speaks a tiny HTTP API — reuse over reinventing a
Node/puppeteer service in-tree. Solvers can't stream 50 MB files, so the actual download stays on
`requests`, replaying the clearance FlareSolverr hands back.

## Design
New module `downloader/cf_solver.py` — a thin FlareSolverr client + a per-host clearance store. It
decouples the page-fetch from the download so no call-site signatures need threading:

- **Enabled iff `FLARESOLVERR_URL` is set** (e.g. `http://flaresolverr:8191`). Unset → every helper is a
  transparent no-op and callers use their current plain-`requests` path. Zero behaviour change when off.
- `solve(url)` → `POST {FLARESOLVERR_URL}/v1` with `{"cmd":"request.get","url":url,"maxTimeout":CF_SOLVER_TIMEOUT}`,
  adding `"proxy":{"url":PROXY_URL}` **only when `PROXY_URL` is set**. Parses the `solution`:
  `response` (HTML), `cookies` (list → dict), `userAgent`. Caches `{cookies, ua}` keyed by URL host.
  Returns the solution dict or `None` on any error/timeout (logged at WARNING, one line — never a
  traceback: a solve miss is an expected skip-and-retry, like the existing CF-403 handling).
- `get_page(url)` → returns solved HTML `str` (or `None`). Used by `get_comic_download_url`.
- `clearance_for(url)` → returns `(cookies_dict, ua)` for the URL's host, solving once and caching if not
  present; `(None, None)` when disabled or unsolvable. Used by `download_file`. CF `cf_clearance` cookies
  are host-scoped and UA-bound, so a gated **mirror** is solved on its own host, then that host's cookies
  are replayed on the streaming download.

## Requirements
- **R1** — `config.py`: add `FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "")`,
  `PROXY_URL = os.getenv("PROXY_URL", "")`, `CF_SOLVER_TIMEOUT = int(os.getenv("CF_SOLVER_TIMEOUT", "60000"))`.
- **R2** — `downloader/cf_solver.py` per Design: `solve`, `get_page`, `clearance_for`, `_enabled()`,
  module-level per-host clearance cache. Proxy included in the payload only when `PROXY_URL` is set.
  All network failures return `None`/empty and log one WARNING line (no traceback).
- **R3** — `get_comic_download_url()` (`downloader/get_comic_download_url.py`): when the solver is enabled,
  fetch the page HTML via `cf_solver.get_page(comic_url)` instead of `requests.get`; on `None`, log +
  return `None` (skip, retry next run). When disabled, the current `requests` path (incl. the 429
  back-off) is unchanged. Parsing/link-selection logic is otherwise untouched.
- **R4** — `download_file()` (`downloader/download_file.py`): before the streaming `requests.get`, call
  `cf_solver.clearance_for(url)`; if it returns clearance, merge the cookies into the request and override
  the `User-Agent` with the solver's UA (must match the UA the cookies were minted with). When disabled or
  no clearance, behaviour is exactly as today. The existing magic-byte challenge guard stays as the
  safety net.
- **R5** — `docker-compose.yml`: add a `flaresolverr` service (`ghcr.io/flaresolverr/flaresolverr:latest`,
  expose `8191`, `restart: unless-stopped`) and set `FLARESOLVERR_URL=http://flaresolverr:8191` on the app
  service. `PROXY_URL` documented (commented) but unset by default. App `depends_on: flaresolverr`.
- **R6 (ponytail)** — no new Python dependency (`requests` already present); no proxy rotation (single
  optional `PROXY_URL`); no clearance TTL/expiry beyond process lifetime — the worker restarts wipe it and
  a stale cookie just re-solves. Mark these ceilings with `ponytail:` comments.

## Definition of done (objective gate)
1. `docker build -f Dockerfile.test -t comics-test .` then
   `docker run --rm -v "${PWD}:/app" -w /app comics-test pytest -q` — full suite green + new tests.
2. **New `tests/test_cf_solver.py`** (mock `requests.post`, no network) proves:
   - disabled (no `FLARESOLVERR_URL`) → `get_page`/`clearance_for` are no-ops (`None`/`(None,None)`) and
     never POST;
   - enabled → `solve` POSTs the correct `/v1` payload, and includes the `proxy` key **iff `PROXY_URL`
     set**; parses HTML + cookies + UA; caches per host so a second `clearance_for` doesn't re-POST;
   - a POST raising / non-200 → `None`, one WARNING, no traceback.
3. Live-verify the parser against a **canned FlareSolverr `solution` JSON** (real response shape) through
   `solve()` with `requests.post` monkeypatched — assert HTML/cookies/UA extracted correctly. (No live CF;
   FlareSolverr is external.)
4. CodeRabbit `-t uncommitted` clean on the diff (no new Critical/Major). Gate compose too (infra).

## Out of scope
- Proxy rotation / provider integration (single optional `PROXY_URL` only).
- Routing the getcomics.org **search** (`comic_search/`) through the solver — this covers the download
  path (`get_comic_download_url` + `download_file`); search can adopt `cf_solver` later if it gets gated.
- Any Node/puppeteer code in-tree; clearance persistence across restarts.
