# Comics Scraper

Self-hosted comics manager with a web UI. Automatically downloads new issues from [getcomics.org](https://getcomics.org), tags them with metadata from [Metron](https://metron.cloud) (ComicVine as fallback), and saves them as `.cbz` files — think Sonarr, but for comics.

## Features

- **Web UI** — *arr-style card grid, series detail with issue tracking, download history, scheduler controls
- **Auto-download** — scheduled scraper finds and downloads new issues for monitored series
- **Metadata tagging** — Metron as primary source, ComicVine as automatic fallback; same normalised output either way
- **Issue monitoring** — selectively monitor individual issues per series; annuals tracked separately
- **Library tools** — scan & retag existing files, preview and apply file renames to standard format
- **Search cache** — remembers paginated getcomics.org results to stop early on re-runs
- **Rate-limit aware** — 2 s delay + 30 s backoff on HTTP 429

## Quick start

```yaml
# docker-compose.yml
services:
  comics-scraper:
    image: nunobifes/comics-scraper-py:latest
    container_name: comics-scraper-py
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - METRON_USER=your_metron_username
      - METRON_PASS=your_metron_password
      - COMICVINE_API_KEY=your_comicvine_api_key   # optional
      - SCHEDULE_INTERVAL_HOURS=24
    ports:
      - "8000:8000"
    volumes:
      - /path/to/comics:/app/comics
      - /path/to/logs:/app/logs
      - /path/to/cache:/app/cache
    restart: unless-stopped
```

```bash
docker compose up -d
```

Open **http://localhost:8000** and add your first series.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `METRON_USER` | — | [metron.cloud](https://metron.cloud) username (free account) |
| `METRON_PASS` | — | metron.cloud password |
| `COMICVINE_API_KEY` | — | ComicVine API key — only needed when a series isn't on Metron |
| `SCHEDULE_INTERVAL_HOURS` | `24` | How often the scraper runs |
| `PUID` / `PGID` | `1000` | UID/GID for file ownership inside the container |
| `TZ` | `Etc/UTC` | Timezone for scheduled runs and log timestamps |

## Volumes

| Mount | Purpose |
|---|---|
| `/app/comics` | Downloaded `.cbz` files |
| `/app/logs` | Log files |
| `/app/cache` | `search_cache.json` + `comics.db` (SQLite) |

## Web UI

| Page | Description |
|---|---|
| `/series` | Card grid of all tracked series with cover art and progress bars |
| `/series/add` | Search Metron and add a new series |
| `/series/{id}` | Series detail — cover, metadata, full issue list with status |
| `/series/{id}/edit` | Edit series metadata and getcomics.org search override |
| `/downloads` | Active downloads + full history, filterable by source/status |
| `/scheduler` | View next run time, configure interval or cron schedule |
| `/library` | Scan all series folders and tag untagged files; force-retag option |

## How the scraper works

1. Loads all **enabled** series from the SQLite database
2. For each series, searches getcomics.org using the series name (or `getcomics_search_name` override)
3. Skips issues already present locally (matched by issue number) and issues not in the monitored set
4. Downloads new issues; converts `.cbr` → `.cbz` if needed
5. Tags each file with Metron metadata (falls back to ComicVine automatically)
6. Waits `SCHEDULE_INTERVAL_HOURS` and repeats

The scraper runs immediately on container start and then on schedule.

## File structure

```
/app/comics/
  {Publisher}/
    {Series Name} ({Year})/
      {Series Name} #{issue}.cbz
      Annuals/
        {Series Name} Annual #{issue}.cbz
```

## Metadata sources

**Metron** is always tried first. If the series or issue is not found, **ComicVine** is used automatically — no manual configuration needed. Both return the same normalised metadata dict (series, issue number, title, publisher, synopsis, cover date).

A Metron account is free. A ComicVine API key is only necessary for series not covered by Metron.

## Migrating from `series_list.txt`

If you used the original file-based version:

```bash
docker exec -it comics-scraper-py python migrate_series_list.py
```

This imports all entries from `series_list.txt` into the SQLite database. The file is no longer read by the scraper after migration.

## Requirements

- Docker + Docker Compose
- [Metron](https://metron.cloud) account (free)
- ComicVine API key — optional, only needed as fallback
