# Migration Journal — comics-scraper-py → React SPA

## §1 — Code-review gate (pre-migration)

CodeRabbit run (free CLI): `--type committed --base-commit 8eea9408` (newer root → HEAD `69a3158`, 77 files; true v1.0 root gives 179 > 150-file free cap). **30 findings: 3 critical, 15 major, 12 minor.**

### Fixed (backend / non-UI)
**Critical**
- `web/app.py` `rename_apply` — path traversal: form-supplied `folder` joined into `os.rename` unchecked. Added `os.path.commonpath` check vs `COMICS_BASE_DIR` for both src & dst. **Root cause:** trusted client-supplied path segments.
- `downloader/check_and_download_comics.py` — issue regex `#(\d+)` truncated `#1.5`→`1`, colliding with `#1`'s file (overwrite/data loss). Now `#(\d+(?:\.\d+)?)`; decimals format as `001.5`. Guard: `downloader/test_issue_format.py`.
- `requirements.txt` — `requests` 2.32.3→2.32.4 (CVE netrc credential leak).

**Major** (same decimal-issue class + crash-robustness)
- `comic_search/search_comics.py` ×2 — decimal regex + `float()` sort key (was `int`, mis-sorted/truncated decimals).
- `retag_comics.py` — `_issue_number` preserves decimals; `expected_filename` formats `001.5` (was `int():03d` crash). Mirrors downloader naming.
- `metadata/get_comic_metadata_metron.py` — `_find_series_id` wrapped `int()` of metron_id/cv_id/year_began (was uncaught crash on bad data).
- `metadata/tag_cbz_file.py` — `strptime` wrapped (invalid `store_date` → warn+skip, not crash); set `meta.comments` too (comicapi version compat for `<Summary>`).
- `util.py` `convert_cbr_to_cbz` — count successful writes before deleting CBR (empty zip is ~22 bytes, size check passed); `os.path.splitext` for extension swap.
- `web/scanner.py` — `datetime.utcnow()` → `datetime.now(timezone.utc)`.

### Deferred → into the SPA migration (templates being deleted)
- **XSS (major):** inline `onclick` interpolation in `partials/log_files.html` (f.name) & `partials/metron_pick_results.html` (field). React components won't have inline handlers by construction — resolved structurally when these pages migrate. Low exploitability meanwhile (log filenames / server-constant field).
- Per-template nitpicks (CDN SRI in `base.html`, label-for in scheduler/xml forms, hardcoded PT strings, checkbox a11y, datetime in scheduler.py, error-message HTML interpolation) — fold into each page's /flow spec.

### Skipped (verified invalid against code)
- `rss_feed.py` `_parse_title`: CodeRabbit wanted `str(int(float(n)))` normalization — would truncate `1.5`→`1`, reintroducing the critical bug. Regex already captures decimals. Also `normalize_title()` on name would lowercase the display string. Not applied.

Verification: `ast.parse` clean on all 8 edited files; `test_issue_format.py` green; retag logic asserted in isolation (comicapi is Docker-only locally).

Note: live comics library for testing at `N:\Comics` (read-only, no changes).

## §2 — Audit (templates · routes · entities)

**Auth:** none. No login/session/middleware in `web/app.py`. "Preserve auth" = no-op; SPA is open same-origin.

**Response split:** 62 routes — 43 return HTML (Jinja pages/partials), ~7 JSON (`/api/series`, `/health`, bulk ops return small dicts), rest are HTMX partials. SPA needs JSON for everything; content-negotiate or add `/api/*` per page.

**Models** (`web/models.py`): `Series`, `MonitoredIssue` (selective per-issue monitor; type regular|annual), `DownloadJob` (queued→done/failed, source manual|scraper, has progress fields), `AppSetting` (kv), `MetronSeriesCache`, `MetronIssueCache`.

**External:** Metron API (primary), ComicVine (fallback), getcomics.org (search + RSS feed).

### Pages → templates → routes → data
| # | Page | Template | Key routes | Data / actions |
|---|------|----------|-----------|----------------|
| 1 | Series list | `series_list.html` (+`series_row`) | GET `/`,`/series`,`/api/series`; bulk `/api/series/bulk/{toggle,monitor,refresh,delete}`; PATCH/POST/DELETE toggle | Series cards: cover, progress (downloaded/total), status colour, enabled/paused; multiselect bulk |
| 2 | Series add | `series_add.html` (+`metron_results`,`metron_pick_results`,`add_form`,`verify_results`) | GET `/series/add`,`/api/metron/search`,`/search-pick`,`/series/{id}/add-form`,`/api/verify-search`; POST `/series` | Metron search-as-you-type, cover preview, live getcomics page-1 verify, create |
| 3 | Series detail | `series_detail.html` (+`series_issues`,`issue_metadata_form`,`series_xml_form`,`rename_preview`) | GET `/series/{id}`,`/issues`; issue monitor/download; metadata GET/POST(+`/from-metron`); series-xml GET/POST; rename preview/apply; POST `/series/{id}/scan`; DELETE issue(s) | Issues table (Downloaded/Upcoming/Missing/TBA), per-issue monitor+download+metadata edit, ComicInfo XML edit, bulk rename, retag scan, delete |
| 4 | Series edit | `series_edit.html` | GET `/series/{id}/edit`; POST `/update`; toggle | Edit form + verify button |
| 5 | Calendar | `calendar.html` | GET `/calendar` | Real month view from `MetronIssueCache.store_date` for monitored series |
| 6 | Releases | `releases.html` (+`releases_list`) | GET `/releases`,`/releases/list` | getcomics RSS matched vs monitored series (`_match_feed_entries`) |
| 7 | Logs | `logs.html` (+`log_files`,`log_stream`) | GET `/logs`,`/logs/stream`,`/logs/files`,`/logs/{f}/download`; DELETE `/logs/{f}`; POST `/logs/cleanup`,`/logs/settings` | Live-updating viewer (stream/poll), file list, download/delete/cleanup, retention setting |
| 8 | Scheduler | `scheduler.html` (+`scheduler_status`) | GET `/scheduler`,`/status`; POST `/run`,`/config` | APScheduler status, run-now, interval/enabled config |
| 9 | Library | `library.html` (+`library_status`) | GET `/library`,`/status`; POST `/library/scan` | Whole-library retag scan + progress |
| 10 | Downloads | `downloads.html` (+`downloads_active`) | GET `/downloads`,`/active`,`/badge`; POST `/cancel`; DELETE job/all | Live queue: progress bar, speed, ETA, cancel |

Shared partials: `toast`, `base.html` (shell/nav).

**Proposed migration order** (deps first): scaffold → 1 Series list → 4 Series edit → 2 Series add → 3 Series detail (biggest) → 10 Downloads → 9 Library → 8 Scheduler → 6 Releases → 5 Calendar → 7 Logs. Each = one /flow cycle, per-page commit + screenshot + pause.

## §3 — Scaffold + Page 1 (Series list)

**Scaffold** (`frontend/`, done inline — mechanical, not spec'd): Vite + React 19 + TS + Tailwind v4
(@tailwindcss/vite plugin, no config file) + shadcn/ui new-york (radix-ui unified pkg) + TanStack Query
+ RHF + Zod + lucide + react-router. Path alias `@/`. Vite proxies **only** `/api` + `/health` →
`127.0.0.1:8000` (SPA owns page routes; proxying `/series` would shadow the client route — root cause of
a class of dev 404s avoided). Design tokens (dark "media library", light+dark, 5 status colours) in
`src/index.css`. **Root cause fix:** `baseUrl` is deprecated in TS7 → dropped it, `paths` alone works
under bundler resolution.

**Backend:** extracted `_series_overview(db)` shared by the Jinja `/series` route and new
`GET /api/series/overview` (JSON) — single source of truth, no drift.

**Frontend:** `AppLayout` (sidebar/nav/theme/mobile) + `SeriesList` page at full parity
(URL-synced filters/sort, status-coloured cards, legend+stats footer, bulk bar wired to bulk endpoints
+ sonner toasts) + `ComingSoon` stubs for the 9 other routes. Theme = `.dark` class, localStorage
`cs-theme`, system default.

**/flow note:** flow-build subagent hit the session limit after 14 tool calls having written nothing —
the orchestrator took over the build. Lesson for the loop: a cold build subagent re-derives context the
orchestrator already holds; for a well-specced page the orchestrator building inline is cheaper and more
robust. Candidate SKILL.md change: allow "orchestrator-inline build" when context is already loaded,
reserve subagent spawn for parallel/independent work.

**Gate:** `npm run build` ✅ green. Live screenshots ✅ match Jinja in light + dark.
CodeRabbit: 6 findings — 2 "critical" were stale-knowledge false positives (`radix-ui@1.6.0` is a real
installed pkg; build resolves it), 1 major (Pydantic models) skipped as over-engineering inconsistent
with existing plain-dict endpoints. **Fixed:** nav active-state collision (Series highlighted on
/series/add — now explicit per-item matchers mirroring base.html) + misleading hex comments in index.css.
Re-verified: only "Add Series" active on /series/add.

Dev verification runtime: `frontend/mock_api.py` (stdlib, gitignored) seeds 6 series across all 5 statuses;
preview via `.claude/launch.json` name `comics-frontend` (port 5173, proxy → mock on 8000). Real backend
is Docker-only locally (sqlalchemy/comicapi not installed on host).

## §4 — Page 2 (Series edit) + shared form JSON infra

**Backend (new JSON, Jinja endpoints untouched):**
- `GET /api/series/{id}` + `PUT /api/series/{id}` — single-series read/update. PUT takes a validated
  `SeriesUpdate` Pydantic model (input at trust boundary → validation kept, unlike the overview *response*
  which stays a plain dict). Shared `_series_dict(s)` serializer.
- `GET /api/metron/results?name=` — normalised Metron search (one flat shape from cache OR API fallback)
  via new `_metron_search_json`. The HTML `/api/metron/search` + `search-pick` left as-is to avoid
  destabilising the Jinja path (their cache/API result shapes differ; templates handle both).
- `GET /api/verify-search/json` — getcomics page-1 verify; extracted pure `_getcomics_verify(term)` now
  shared by both the HTML and JSON endpoints.

**Frontend:** `SeriesEdit` page (`/series/:id/edit`) — RHF + Zod, prefill via `key={id}` remount so the
form mounts once with values (avoids the controlled-input prefill race). Inline Metron annual search
(debounced TanStack query → click sets the ID), getcomics Verify (lists page-1 hits), issue_min with the
"currently #N" helper. Save → PUT → invalidate overview+detail → navigate to detail.

**Gate:** `npm run build` ✅. Live-verified light+dark: prefill correct, annual search returns + click sets
ID 9001, Verify lists results, nav "Series" active on /series/:id/edit (not "Add Series"). CodeRabbit: 1
minor (`a["href"]` KeyError in `_getcomics_verify`) → fixed with `if a.get("href")` guard.

**Root cause caught this page:** the Python-template `.gitignore` has unanchored `lib/` + `[Ll]ib` rules
that silently matched `frontend/src/lib/` → `api.ts`/`theme.ts`/`utils.ts` were NOT committed in Page 1
(§3). Build passed locally only because the files exist on disk; a clean clone would have failed. Fixed by
appending `!frontend/src/lib/` negations; this commit restores the three files. Lesson: after a commit that
adds a new dir, `git ls-files <path>` to confirm new files actually landed — gitignore can eat them invisibly.

## §5 — Page 3 (Add series)

**Backend:** `POST /api/series` (validated `SeriesCreate(SeriesUpdate)` model + cover/total_issues) returning
the created series; added `cv_id` to `_metron_search_json` so the SPA prefills the CV Volume ID directly from
the selected search result — no separate add-form endpoint needed (the Jinja `/add-form` route stays for the
old UI).

**Frontend:** `SeriesAdd` (`/series/add`) — two-step: debounced Metron search (reuses `/api/metron/results`)
→ result cards → Select → prefilled form (cover header, publisher/name/year/metron_id/cv_id, getcomics
verify) → POST create → toast + navigate to /series. Extracted shared `components/Field.tsx` (now used by
both Add and Edit — removed Edit's inline copy).

**Gate:** `npm run build` ✅. Live-verified: search returns cards, Select prefills (metron_id 9001, publisher,
year), form matches add_form.html, nav "Add Series" active. CodeRabbit: 3 majors, all fixed —
(1) blank publisher/series_name now rejected by a `field_validator` on `SeriesUpdate` (covers create+update,
the trust-boundary validation the SPA's Zod can't enforce server-side); (2) duplicate (publisher,name,year)
→ caught `IntegrityError` → 409 instead of 500 (both create + update); (3) `intOrNull` could emit NaN→null
silently → hardened to a shared `parseIntOrNull` in lib/utils (used by Add + Edit).

## §6 — Page 4 (Series detail — the big one)

**Backend (one block of JSON endpoints, all reuse existing helpers; Jinja routes untouched):**
`GET /api/series/{id}/detail` (header + local_count), `GET /api/series/{id}/issues` (regular+annual lists,
monitored sets, has_monitoring, cached_at, rate_limited passthrough), `POST .../issues/{n}/monitor`,
`POST|DELETE .../monitor-all` (reuse the HTML handlers' DB logic directly), `POST .../issues/{n}/download`,
`DELETE .../issues/{n}`, `POST .../scan`, `DELETE /api/series/{id}`, metadata GET (`?source=metron`)/POST,
series-xml GET/POST, rename-preview GET / rename-apply POST (same `commonpath` traversal guard as §1).

**Frontend:** `SeriesDetail` (`/series/:id`) — header (cover, status, meta, progress, Edit/Pause/Scan/
Preview-Rename/Delete), `IssuesTable` (regular + annual sections; per-row monitor bookmark, status badges
Downloaded/Missing/Upcoming/TBA, missing→click-to-download, downloaded→edit+delete, bulk-select checkboxes),
bulk-delete bar, footer (cached_at, Selective badge, Monitor/Unmonitor all), rate-limit auto-retry via
`refetchInterval`. Extracted components `MetadataSheet` (shadcn Sheet, ComicInfo EDITOR_FIELDS + "Load from
Metron" + save) and `SeriesNotes` (collapsible series.xml editor). Added shadcn sheet/textarea/label/separator.

**Gate:** `npm run build` ✅. Live-verified (dark): all 6 issue rows + statuses, monitored bookmarks filled
for 1-3 / dimmed 4-5 (selective mode), Annuals section, cached/selective footer, Series Notes expands (6
fields), metadata Sheet opens ("Issue #1 — Edit Metadata", 18 fields). CodeRabbit: 1 critical (false
positive — claimed the `.gitignore` lib negation was removed; verified present + lib files tracked) + 5
minor. Fixed: `commonpath` ValueError guard on the JSON rename-apply (cross-drive on Windows); the two
form `useEffect`s (MetadataSheet, SeriesNotes) now populate once-per-issue/open via a ref so a background
refetch can't clobber unsaved edits. Skipped: vendored `"use client"` in label.tsx; monitor-all existence
check (the reused handler already 404s / unmonitor is idempotent).

**/flow note:** biggest page; orchestrator-inline build again proved right (a cold subagent would re-read
~6 templates + 13 handlers). The `sed` prop-strip trimmed a shared-line prop by accident → caught by the
`npm run build` gate immediately. Reinforces: the objective gate, not the edit, is the safety net.

## §7 — Page 5 (Downloads)

**Backend:** JSON endpoints `GET /api/downloads` (history), `/api/downloads/active` (+ per-job
`progress {bytes,total,rate_bps}` from worker), `/api/downloads/badge` ({count}), `DELETE /api/downloads/{id}`,
`POST /api/downloads/{id}/cancel`, `DELETE /api/downloads` (clear). Shared `_job_dict` serializer. Jinja
routes untouched.

**Frontend:** `Downloads` page (`/downloads`) — Active section (poll 3s via `refetchInterval`, progress bar +
bytes/%/speed/ETA, cancel) + History table (source filters all/scraper/manual/failed, status badges,
fail-error display, per-row remove, Clear All). **Bonus:** wired the live download count badge into the
sidebar Downloads nav (poll 5s) — resolves the slot deferred in §3.

**Gate:** `npm run build` ✅. Live-verified via DOM eval (all 5 statuses, both sources, 4 filters, progress
ETA/MB, fail error, Clear All, sidebar badge "2"). Note: `preview_screenshot` times out on this page — the
3s active-poll keeps the network busy so the tool never sees "idle"; not a page bug (eval confirms render).
CodeRabbit: 2 — fixed api_download_delete 404-vs-409 (None job now 404) + unknown-typed catch guard.
