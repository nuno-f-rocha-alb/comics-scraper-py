# spec: download-staging (intermediary download folder)

## Objective
Stop writing in-progress / untagged comics into the Komga library folder. Today `download_file()` writes
the final-named file straight into the destination series folder, and `process_downloaded_comic()` then
converts `.cbr→.cbz` and writes ComicInfo metadata **in place** — so Komga can index a file before it's
tagged, and the cbr→cbz step momentarily leaves two files. Instead: download + convert + tag + final-name
in a **staging folder**, then move the single finished `.cbz` into the destination so Komga only ever sees
a complete, tagged file appear.

## Decisions (from spec interview)
- **Staging location:** a hidden `.downloads` subfolder **inside the comics volume**
  (`COMICS_BASE_DIR/.downloads`) — same filesystem as the destination, so the final landing is an atomic
  rename. The app creates it on demand (chowned to PUID:PGID).
- **Timing:** convert (`.cbr→.cbz`) + ComicInfo tagging + final naming all happen **in staging**; only the
  finished `.cbz` is moved to the destination.

## Current flow (to change)
- `downloader/download_file.py:download_file(url, save_dir, series, issue, year, ...)` → streams to
  `save_dir/Name #NNN (year).ext.part`, atomic-renames to the final name **in save_dir**. Callers pass the
  destination as `save_dir`.
- `downloader/process_downloaded_comic.py:process_downloaded_comic(entry, file_path, issue)` → cbr→cbz
  (writes alongside), `tag_cbz_file` in place, `os.chown`. Returns None.
- Callers: `web/worker.py:_download_issue` (manual/RSS path) and
  `downloader/check_and_download_comics.py` (scheduled search; **batch-processes metadata at the end**).

## Requirements

### R1 — staging directory helper
- `STAGING_SUBDIR = ".downloads"` constant (config.py). Leading dot so Komga's scanner ignores it
  (verify in Komga — most scanners skip dotfolders; this is an ops check, see Out-of-band).
- `staging_dir() -> str` (util.py): returns `os.path.join(COMICS_BASE_DIR, STAGING_SUBDIR)`, `makedirs`
  on demand, `os.chown(PUID, PGID)`. One flat folder (filenames already carry series+issue+year → no
  collision across series).

### R2 — `download_file` writes to staging (no signature change)
- Callers pass `staging_dir()` as `save_dir`. `download_file` is otherwise unchanged: `.part` scratch +
  atomic rename to the final name **within staging**. Its existing `.part` cleanup on failure/cancel stays.

### R3 — `process_downloaded_comic` returns the final path
- After cbr→cbz it reassigns `file_path`; **return that path** (currently returns None) so the caller knows
  the finished `.cbz` to move. Keep convert + tag here; **move the `os.chown` out** (chown the file in the
  destination in R4, after the move).

### R4 — `install_to_library(staged_path, dest_dir) -> str` (new, util.py or downloader/install.py)
Move the finished file from staging into the destination atomically:
1. `os.makedirs(dest_dir, exist_ok=True)`.
2. `final = os.path.join(dest_dir, os.path.basename(staged_path))`.
3. Land it without ever exposing a partial file to Komga: copy/move to a hidden temp name in the
   **destination** dir — `tmp = os.path.join(dest_dir, "." + os.path.basename(staged_path) + ".tmp")` via
   `shutil.move(staged_path, tmp)` (handles the case where staging and dest are on different mergerfs
   branches / filesystems), then `os.replace(tmp, final)` (atomic within the destination dir).
4. `os.chown(final, PUID, PGID)`; return `final`.
- If `final` already exists (re-download), `os.replace` overwrites it atomically — keep current
  overwrite-on-redownload behaviour.

### R5 — worker path (`web/worker.py:_download_issue`)
- Download to `staging_dir()`; `staged = process_downloaded_comic(entry, save_path, issue_number)`;
  `installed = install_to_library(staged, create_series_directory(entry))`; return
  `os.path.basename(installed)`. (Cancellation still cleans the staging `.part` via `download_file`.)

### R6 — scraper path (`downloader/check_and_download_comics.py`)
- Both the regular and annual branches: `download_file(..., staging_dir(), ...)` instead of the dest dir.
- Carry the destination per item into the batch list: append `(proc_entry, staged_path, issue_number,
  dest_dir)` (dest_dir = the regular series dir or the `Annuals` dir).
- Batch step at the end: for each item, `staged = process_downloaded_comic(proc_entry, staged_path, num)`
  then `install_to_library(staged, dest_dir)`.
- The "already exists" pre-checks (`comic_file_regex` over the destination listing) stay — they correctly
  gate against files already installed in the destination from previous runs. (Known minor: the in-run
  `existing_files` re-scan after each download no longer sees just-downloaded files until they're installed
  at batch end; duplicate issue URLs in one run are not expected, so accept it — `ponytail:` note in code.)

### R7 — leftover staging cleanup
- On worker start (`web/worker.py:start`, alongside the existing stuck-job re-enqueue), remove any leftover
  files in `staging_dir()` (orphans from a crash between download and install). Downloads always re-fetch,
  so deleting partial/abandoned staging files is safe.

## Out of scope (ponytail)
- No new download volume/mount (staging lives in the comics volume by decision).
- No per-series staging subfolders (flat dir; names don't collide).
- No change to metadata sources, naming scheme, or the cbr→cbz logic itself — only *where/when* they run.
- No UI changes (backend-only).
- Retry/resume of a partially-downloaded staging file across restarts (we re-download — R7 just cleans up).

## Out-of-band (ops, not code)
- Verify Komga ignores `COMICS_BASE_DIR/.downloads` (dotfolder). If it doesn't, exclude it in Komga's
  library settings. Document in CLAUDE.md.

## Definition of done (objective gate)
1. **Docker pytest** green (existing + new `tests/test_install.py`, all temp dirs, no network):
   - `install_to_library` moves a staged file into dest (final name correct), source removed, returns final
     path; overwrites an existing dest file; lands via a dot-temp then atomic `os.replace` (assert no
     non-dot partial appears — e.g. final exists and `.*.tmp` gone).
   - cross-filesystem move path works (simulate by pointing staging + dest at two separate temp trees and
     stubbing/forcing the `shutil.move` branch).
   - `process_downloaded_comic` returns the final `.cbz` path (cbr→cbz mocked like `test_convert_cbr`).
   - worker integration (Metron/network mocked): after `_download_issue`, the file is in the series dir and
     `staging_dir()` no longer contains it.
2. **CodeRabbit** clean on changed files — via WSL Debian
   (`wsl -d Debian -e sh -lc "cd /mnt/c/Users/nunob/Repositorios/comics-scraper-py && coderabbit review --agent -t uncommitted"`).
3. **Live verify**: run `install_to_library` with real temp dirs in the test container and assert the file
   is in dest + absent from staging (the pytest run is the runtime check). No frontend → no `npm build`.
