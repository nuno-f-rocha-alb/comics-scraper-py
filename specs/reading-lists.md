# spec: reading-lists (Phase A — core)

## Objective
Discover Metron reading lists, add one to the app so it pulls in the right series **monitoring only the
issues the list needs**, see per-issue ownership status, and get the list into Komga via a generated **CBL
file** and/or a **direct Komga API push**. Reading-list data is **backed up locally** (DB) so Metron is hit
only on add / explicit re-sync — never on every page view.

Phase B (NOT in this spec): auto-suggest lists with ≥X% already-owned issues (needs a bounded background
scan; deferred).

## Decisions (from interview)
- **Phasing:** core now (search + add + monitor + CBL + Komga push). Auto-suggest deferred to Phase B.
- **Monitor scope:** user picks which `issue_type`s to monitor when adding (Core Issue / Tie-In / Prologue /
  Epilogue). All items are still **stored** (for CBL + status); the filter only decides what gets monitored
  → downloaded.
- **Komga:** both a downloadable `.cbl` **and** a direct push to Komga's API.
- **Backup/cache:** added lists live in local DB tables (the backup). Search results use a short-TTL
  in-memory cache. Metron is called only on search (cache-miss), preview, add, and re-sync.

## Source facts (from Metron API + the sample CBL)
- Metron `/api/reading_list/` (list, filters: `name`, `publisher`, `list_type`, `attribution_source`,
  `average_rating__gte`, `is_private`, `modified_gt`; page size 50). `/{id}/` detail (desc, image,
  items_url). `/{id}/items/` (page 50, ordered by `order`): each item →
  `issue{ id, series{id,name,volume,series_type}, number, cover_date, store_date, cv_id }, order, issue_type`.
  **Read-only API.** Default to `is_private=false`.
- Metron item gives the **issue cv_id** but not the **series cv id** or series `year_began`; pull those from
  the series detail (`/api/series/{id}/`) when creating the local series (the existing add path already does
  this). For CBL `Volume` use the series **start year**, for `Year` use the item's `cover_date` year.
- CBL schema (matches sample): `<ReadingList><Name/><NumIssues/><Books><Book Series Number Volume Year>
  <Database Name="cv" Series Issue/></Book>…</Books><Matchers/></ReadingList>`. Komga matches books by
  **Series name + Number** primarily, so CBL `Series` must equal what Komga sees (= ComicInfo Series =
  Metron series name, which is what the app already tags).

## Data model (new tables, created via the existing migrate-on-startup path)
- **ReadingList**: `id` (local PK), `metron_id` (unique), `name`, `slug`, `list_type`, `attribution_source`,
  `attribution_url`, `image_url`, `desc`, `average_rating`, `num_items`,
  `monitored_issue_types` (CSV; empty = all), `added_at`, `synced_at`.
- **ReadingListItem**: `id` (local PK), `reading_list_id` (FK, indexed), `order`, `issue_type`,
  `metron_issue_id`, `metron_series_id`, `series_name`, `series_year` (year_began, nullable),
  `number`, `cover_year` (nullable), `cv_issue_id` (nullable), `cv_series_id` (nullable),
  `series_id` (FK→Series, nullable; set when matched/created). Unique (`reading_list_id`,`metron_issue_id`).

These rows are the local backup: CBL, status, and monitoring all read from here.

## Requirements

### R1 — Metron reading-list client (`metadata/metron_client.py` or a new `metadata/metron_reading_lists.py`)
- `search_reading_lists(**filters) -> list[dict]` → GET `/reading_list/` with `is_private=false` forced + the
  passed filters; one page (50). Uses the existing rate-limited `metron_client.get` (`block=False` in web
  handlers → surfaces RateLimitedError like other web Metron calls).
- `get_reading_list_detail(metron_id) -> dict` and `get_reading_list_items(metron_id) -> list[dict]`
  (follow pagination — `next` — to collect all items).

### R2 — Search (cached) — `GET /api/reading-lists/search`
- Query params mirror the Metron filters (`name`, `publisher`, `list_type`, `attribution_source`,
  `average_rating__gte`). Server-side **in-memory TTL cache** (e.g. 1h) keyed by the normalised query →
  repeated/parameter-tweak searches don't re-hit Metron. Returns list cards (id, name, list_type,
  attribution, average_rating, image, num? ). 
- `ponytail:` in-memory cache (lost on restart) — fine for search; the *added* lists are the durable backup.

### R3 — Preview before add — `GET /api/reading-lists/metron/{metron_id}/preview`
- Fetch detail + all items. For each item annotate against the local library:
  `series_tracked` (a local Series has `metron_series_id == item.series_id`), `owned` (local file exists for
  that series+number — reuse `_local_issue_numbers`), and group/summary counts per `issue_type`.
- Used by the add UI to show what will happen and to offer the issue_type checkboxes. Cache the fetched items
  (so the subsequent POST add can reuse them without re-fetching).

### R4 — Add a list — `POST /api/reading-lists`
- Body: `{ metron_id, issue_types: [str] | null }` (null/empty = monitor all types).
- Steps (idempotent — re-adding upserts):
  1. Persist `ReadingList` + all `ReadingListItem`s (full list, regardless of issue_types).
  2. For each **distinct** `metron_series_id`: find local Series by `metron_series_id`; if absent, **create**
     it via the existing Metron series-add logic (fetch series detail → publisher/year_began/cover/total),
     reuse `api_create_series` internals — do **not** duplicate the create code.
  3. Link items → `series_id`.
  4. For items whose `issue_type` ∈ selected set: add `MonitoredIssue(series_id, number, "regular")`
     (merge — don't clear the series' existing monitoring; INSERT OR IGNORE on the unique constraint).
- Returns the stored list dict. The scraper/RSS will now pick up the monitored issues (existing machinery).

### R5 — Reading-lists pages (local, no Metron call)
- `GET /api/reading-lists` → added lists with progress `{owned, total}`.
- `GET /api/reading-lists/{id}` → items with **live status** each: `owned` > `monitored` > `missing`
  (+ `untracked` if series not created). Computed from local DB + filesystem only.
- `DELETE /api/reading-lists/{id}` → remove the list + its items. **Leaves series, files, and monitoring
  untouched** (overlap with other lists makes auto-unmonitor unsafe in Phase A — documented).

### R6 — Re-sync — `POST /api/reading-lists/{id}/resync`
- Re-fetch items from Metron, upsert `ReadingListItem`s (add new, update changed, drop removed), re-link
  series, and monitor any **new** items matching the stored `monitored_issue_types`. Update `synced_at`.

### R7 — CBL export — `GET /api/reading-lists/{id}/cbl`
- Generate the CBL XML from stored items in `order`. `Name`=list name, `NumIssues`=count. Per item:
  `Series=series_name`, `Number=number`, `Volume=series_year` (fallback cover_year), `Year=cover_year`;
  include `<Database Name="cv" Series=cv_series_id Issue=cv_issue_id/>` only when ids are present.
  Includes **all** items (true reading order); Komga skips books it can't match. Response is a file download
  (`Content-Disposition: attachment; filename="<name>.cbl"`, `application/xml`).

### R8 — Direct Komga push — `POST /api/reading-lists/{id}/push-komga`
- Komga config via **env vars** (`config.py`, like `METRON_USER`/`PUID`): `KOMGA_URL`, `KOMGA_API_KEY` —
  added to `docker-compose.yml`. Auth header `X-API-Key`. If unset, the push route returns a clear 400
  ("Komga not configured") and the UI hides/disables the button.
- Push: for each item, resolve the Komga **book id** (Komga search by series name + number), collect ids in
  list order, then `POST {komga_url}/api/v1/readlists` with `{name, summary, ordered:true, bookIds:[…]}`.
  Best-effort: report `{created: bool, matched: n, unmatched: [items]}`. If a read list with the same name
  exists, update it (`PATCH /api/v1/readlists/{id}`) — keep it idempotent.
- `ponytail:` Komga matching is name+number best-effort; surfaces unmatched rather than failing the whole push.

### R9 — Frontend
- New **Reading Lists** nav entry + route. Pages: search (filters + result cards), list detail (items table
  with status badges reusing the issue-status styling, CBL download button, "Push to Komga" button), and the
  add flow (preview + issue_type checkboxes). Use the existing styled `useConfirm` for destructive actions
  and `toast` for feedback. Extend `frontend/mock_api.py` with the new endpoints for preview verification.

## Out of scope (ponytail / Phase B+)
- Auto-suggest ≥X% owned (Phase B — bounded background scan + cache + threshold setting).
- ComicVine reading lists (Metron-first, matching the rest of the app).
- Un-monitoring on list delete; reading-order-aware downloading (order doesn't affect what's fetched).
- Editing list contents (Metron API is read-only; we mirror, not author).

## Definition of done (objective gate)
1. **Docker pytest** green incl. new `tests/test_reading_lists.py` (all external boundaries mocked —
   `metron_get`/HTTP, temp DB, temp comics dir, Komga HTTP stubbed):
   - parse Metron items → `ReadingListItem` rows (fields mapped correctly incl. cover_year from cover_date).
   - add: creates a missing series (find-or-create, no dup), links items, and monitors **only** the selected
     `issue_type`s (e.g. Core only ⇒ tie-in numbers are NOT in MonitoredIssue), merging with pre-existing
     monitoring without clearing it.
   - status: an item with a local file → `owned`; monitored-but-no-file → `monitored`; neither → `missing`.
   - CBL: valid XML, correct `NumIssues`, a `Book` with the right attributes, `Database` present when cv ids
     exist and omitted when not; parses back with ElementTree.
   - Komga push: builds the correct `bookIds` order + payload and calls the (mocked) Komga client; unmatched
     items reported, push doesn't crash on a missing book.
2. **Frontend build** green (`cd frontend && npm run build`); Reading Lists reachable in nav; verified
   against `mock_api.py` via the preview (search renders, add flow, status badges, CBL button).
3. **CodeRabbit** clean on changed files (WSL Debian, `-t uncommitted`).
4. **Live verify:** the pytest run is the runtime gate; Komga push is best-effort and needs a live verify
   against the user's Komga instance (note in the PR/summary — can't be fully verified here).
