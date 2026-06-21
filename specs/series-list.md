# Spec: Series list page (React SPA — Page 1)

## Objective
Recreate the Jinja **Series list** page (`web/templates/series_list.html`) and the **app shell**
(`web/templates/base.html`) as React, at EXACT functional parity, inside the new `frontend/` SPA.
Old Jinja routes keep working untouched. Frontend lives at SPA route `/series` (and `/`).

## Already done (do not redo)
- `frontend/` scaffolded: Vite + React + TS + Tailwind v4 + shadcn/ui (new-york), TanStack Query,
  RHF + Zod, lucide-react, react-router-dom. Path alias `@/`. Vite proxies `/api` + `/health` →
  `127.0.0.1:8000`. Design tokens (dark "media library", light+dark, status colours) in `src/index.css`.
  shadcn components present: button, card, badge, input, select, dropdown-menu, sonner.
- Backend JSON endpoint **`GET /api/series/overview`** returns `{ series: [...], stats: {...} }`:
  - series item: `id, publisher, series_name, year, cover_image_url, total_issues, enabled,
    metron_series_id, comicvine_volume_id, getcomics_search_name, local_count, status`
  - `status` ∈ `continuing-complete | ended-complete | missing-monitored | missing-unmonitored | downloading`
  - `stats`: `series, ended, continuing, monitored, unmonitored, issues_total, files_total`
- Bulk action endpoints already exist (POST JSON `{ids, ...}` → `{updated|deleted}`):
  `/api/series/bulk/toggle` `{ids, action: pause|resume}`, `/api/series/bulk/monitor` `{ids, mode: all|missing|future|none}`,
  `/api/series/bulk/refresh` `{ids}`, `/api/series/bulk/delete` `{ids}`.
  Also `POST /api/metron/cache/refresh`, `POST /api/sync-covers` (form posts → redirect; call as POST, then refetch).

## Build

### A. App shell (`AppLayout`) — parity with base.html
- Fixed 220px left sidebar, bg `#12141a` feel (use tokens), logo block ("Comics Scraper" / "by nunobifes").
- Section label "Library" + nav links in this order, each with a lucide icon + active state
  (accent `--status-continuing` / primary, left-border highlight):
  Series (`/series`), Add Series (`/series/add`), Calendar (`/calendar`), Releases (`/releases`),
  Downloads (`/downloads`) **with a live count badge** (poll `GET /downloads/badge` shape or `/api/...`;
  if no JSON badge endpoint, poll every 5s and show count — OK to defer the number to a follow-up if no
  JSON source, but keep the slot), Scheduler (`/scheduler`), Library (`/library`), Logs (`/logs`).
  Unmigrated routes render a small "Coming soon" placeholder page (client routes exist, no dead links).
- Theme toggle in footer: light/dark via `.dark` class on `<html>`, persisted in `localStorage` key
  `cs-theme`, default = system preference. Sun/moon icon + label swap.
- Mobile (<768px): sidebar slides off-canvas, hamburger top bar, click-outside closes.
- Main content area holds the routed page.

### B. Series list page — parity with series_list.html
Data from `GET /api/series/overview` via TanStack Query. Requirements:
1. **Header:** title "Series", subtitle "{visibleCount} series tracked" (updates with filters:
   "{n} of {total} series" when filtered). Buttons: "Refresh Cache" (POST `/api/metron/cache/refresh`),
   "Sync Covers" (POST `/api/sync-covers`), "Add Series" (link → `/series/add`).
2. **Empty state** (no series): centred card, book icon, "No series yet", Add Series CTA.
3. **Filter bar:** search box (filters by series_name, case-insensitive), publisher `<select>` (unique
   publishers sorted), status `<select>` (All / Monitored / Paused — maps enabled), sort `<select>`
   (name-asc default, name-desc, publisher-asc, year-desc, year-asc, progress-desc, progress-asc),
   "Select all visible" button, "Reset" link (shown only when any filter active).
   Filter/sort state synced to URL query params (`q, publisher, status, sort`) and restored on load.
4. **Card grid:** `repeat(auto-fill, minmax(148px, 1fr))`. Each card:
   - 2:3 cover (image or book-icon placeholder), "Paused" badge overlay when `!enabled`.
   - title (2-line clamp), "publisher · year".
   - progress: if `total_issues` → thin bar at `local_count/total_issues %` + "{local_count} / {total}" label;
     elif `local_count` → "{local_count} downloaded" label; else nothing.
   - **Progress bar / label colour reflects `status`** (the 5 status tokens — exact mapping in index.css).
   - whole card links to `/series/{id}` (SPA route — placeholder until Page 3).
   - hover-revealed checkbox (top-left) for bulk select; checkbox click must NOT navigate.
5. **No-results** message when filters match nothing.
6. **Footer card:** legend (5 status swatches + labels) + stats row from `stats`
   (Series, Ended, Continuing, Monitored, Unmonitored, Issues, Files).
7. **Bulk action bar** (fixed bottom, visible only with ≥1 selected): "{n} selected", Pause, Resume,
   "Set Monitoring" dropdown (All / Missing only / Future only / None), "Refresh from Metron", Delete
   (confirm dialog: "Delete N series from the DB? Local files are not touched."), Clear. Each calls the
   matching bulk endpoint, toasts result (sonner), then refetches the query. Selected cards get an outline.

## Constraints (ponytail + quality bar)
- Build only this page + shell. No speculative abstractions, no extra pages beyond "Coming soon" stubs.
- Reuse shadcn components; don't hand-roll what's installed.
- a11y: `:focus-visible` on all interactive els, `prefers-reduced-motion` respected, ≥44px touch targets,
  SVG (lucide) icons only (no emoji), 150–300ms transitions, proper labels/aria on filter controls.
- Light AND dark both correct.

## Definition of done (objective gate)
1. `cd frontend && npm run build` exits 0 (this is the real typecheck — NOT bare `tsc --noEmit`).
2. CodeRabbit clean (no Critical/Major) on the changed files.
3. Every numbered requirement above present and wired to real endpoints.
4. Live screenshot (dev server, seeded/mock data) matches the Jinja page in light + dark.
