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
