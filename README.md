# Comics Scraper

Dockerized Python scraper that downloads new comic issues from [getcomics.org](https://getcomics.org), tags them with metadata from [Metron](https://metron.cloud) (with ComicVine as fallback), and saves them as `.cbz` files.

## How it works

1. Reads `series_list.txt` from the comics volume
2. For each series, searches getcomics.org for new issues
3. Skips issues already present locally (matched by issue number)
4. Downloads new issues and converts `.cbr` → `.cbz` if needed
5. Tags each file with metadata (series, issue, title, publisher, synopsis, date)
6. Sleeps 24 hours and repeats

## Setup

### 1. Configure `docker-compose.yml`

Fill in the volume paths and credentials:

```yaml
environment:
  - PUID=1000          # UID for file ownership
  - PGID=1000          # GID for file ownership
  - METRON_USER=...    # metron.cloud username
  - METRON_PASS=...    # metron.cloud password

volumes:
  - /your/comics/path:/app/comics   # series_list.txt must be here
  - /your/logs/path:/app/logs
  - /your/cache/path:/app/cache     # search cache lives here
```

### 2. Create `series_list.txt`

Place this file inside your comics volume (`/app/comics/series_list.txt`).

Format:
```
Publisher/Series Name/Start Year/ComicVineVolumeID
Publisher/Series Name/Start Year/ComicVineVolumeID/AnnualVolumeID
```

- Lines starting with `#` are comments
- `Start Year` filters out issues older than that year
- `ComicVineVolumeID` is the numeric ID from a ComicVine volume URL — used for metadata lookup on both ComicVine and Metron
- `AnnualVolumeID` is optional — if present, annuals are downloaded to `{Series}/Annuals/`

Example:
```
# Marvel
Marvel/Amazing Spider-Man/2022/12345
Marvel/X-Men/2021/67890/11111

# DC
DC/Batman/2023/54321
```

### 3. Run

```bash
docker compose up -d
```

Logs are written to the `logs/` volume and to stdout.

## Metadata

Metron is the primary metadata source. If a series or issue is not found on Metron, the scraper automatically falls back to ComicVine.

The `ComicVineVolumeID` in `series_list.txt` is also used to look up the corresponding series on Metron via its `cv_id` filter — no separate Metron ID needed.

A ComicVine API key is bundled in `config.py`. Metron credentials must be provided via environment variables.

## File structure

Downloaded files are saved as:
```
/app/comics/{Publisher}/{Series Name} ({Year})/{Series Name} #{issue}.cbz
/app/comics/{Publisher}/{Series Name} ({Year})/Annuals/{Series Name} Annual #{issue}.cbz
```

## Search cache

`/app/cache/search_cache.json` remembers every URL seen on getcomics.org per series. This allows the scraper to stop paginating early on subsequent runs — important for series with many search results.

## Requirements

- Docker + Docker Compose
- [Metron](https://metron.cloud) account (free)
- ComicVine API key (already in `config.py`)
