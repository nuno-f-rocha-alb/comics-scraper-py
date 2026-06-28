# spec: cache-first Metron (no passive page-open ever blocks on Metron)

## Objective
Opening a series page (or any passive view) must read from the local cache and **never** wait on Metron.
Metron is contacted in only two situations:
1. the **nightly background refresh** (blocking, has all night to sleep through burst limits), and
2. a **one-time cache-miss fallback** (cache empty → one non-blocking fetch, then it's cached).

Explicit user actions stay live (Add-Series search, "Fetch from Metron" in the metadata sheet, the manual
refresh button, reading-list search/preview) — those are intentional, not passive.

## Root cause (today)
`web/app.py:_get_or_fetch_metron_issues` returns cache only while it's < 7 days old; once stale it **refetches
on page open**. The per-issue title-detail calls then trip Metron's burst safeguard, and the series page
shows "rate limited / wait" every time after the cache ages out. The route already passes `block=False`
(so it doesn't truly hang), but it still goes to Metron and returns empty + a wait hint.

## Requirements

### R1 — series issues route is cache-only
- Add `refresh_if_stale: bool = True` to `_get_or_fetch_metron_issues`. When **False**: return cached rows if
  **any** exist (ignore the 7-day TTL); only hit Metron when the cache is **empty**.
- `api_series_issues` (regular + annual) calls with `refresh_if_stale=False` and keeps `block=False`, so:
  - warm cache → instant, zero Metron calls;
  - cold cache → one non-blocking attempt; if burst-limited it returns the existing `rate_limited` hint and
    the nightly job fills the cache (next open is instant).
- Other callers keep the default (`refresh_if_stale=True`) — unchanged.

### R2 — nightly refresh job
- New APScheduler cron job `metron_nightly` in `web/scheduler.py`, default **03:00** daily
  (`METRON_NIGHTLY_CRON` env, standard 5-field crontab; default `0 3 * * *`). Runs on startup scheduling only
  (not immediately).
- It refreshes, for every **non-ended** series (`not _is_series_ended(s)`): series meta (cover / total /
  status) **and** the full issue cache **including titles** (`skip_titles=False`), for both the regular and
  annual Metron series ids. Runs **blocking** (`block=True`) so it sleeps through burst limits.
- Reuse the existing background machinery: extend `web/metron_refresh.run_refresh` with
  `skip_titles: bool = True` and `only_active: bool = False`; the nightly job calls
  `run_refresh(force=True, skip_titles=False, only_active=True)`. The manual button keeps today's behaviour
  (`skip_titles=True`, all series). Thread `skip_titles`/`only_active` through `_refresh_one_series`.
- Single-flight: if a refresh is already running, the nightly trigger is a no-op (existing `_running` guard).

### R3 — audit (no code unless a passive blocker is found)
- Confirm no other passive page-open route calls Metron blocking. Known live calls are all explicit user
  actions (search / preview / fetch-from-metron) and already use `block=False` — leave them.

## Out of scope (ponytail)
- Re-architecting the cache or adding a new cache store (MetronIssueCache already exists).
- Caching arbitrary Add-Series / reading-list *search* queries beyond the existing short TTL (those are
  deliberate live discovery).
- Changing the 7-day TTL meaning for the manual refresh / scraper paths.

## Definition of done (objective gate)
1. **Docker pytest** green incl. new cases:
   - `api_series_issues` with a **stale** (older than the TTL) non-empty cache returns the cached issues and
     makes **no** Metron call (mock `metron_client.get` to raise if called).
   - empty cache → it does attempt a fetch (mock returns issues) and caches them.
   - `run_refresh(only_active=True)` skips ended series (mock: an ended series is not refreshed).
2. **Frontend build** green (types only; the SPA already handles the issues payload — no UI change needed).
3. **CodeRabbit** clean on changed files.
4. Manual reasoning check documented: after a week (stale cache) opening a series page does 0 Metron calls.
