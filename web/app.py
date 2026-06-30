import logging
import os
import re
import time
import requests
from contextlib import asynccontextmanager
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
log = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from web.database import SessionLocal, init_db
from web.models import (
    AppSetting, DownloadJob, MetronCache, MetronIssueCache, MonitoredIssue,
    ReadingList, ReadingListItem, Series,
)

COMICS_BASE_DIR = os.getenv("COMICS_BASE_DIR", "/app/comics")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from web import worker
    worker.start()
    yield


app = FastAPI(title="Comics Scraper", lifespan=lifespan)


def get_db():
    with SessionLocal() as db:
        yield db


def _series_dir(s: Series) -> str:
    from util import sanitize_filename
    return os.path.join(COMICS_BASE_DIR, sanitize_filename(s.publisher), f"{sanitize_filename(s.series_name)} ({s.year})")


def _count_local_issues(s: Series) -> int:
    path = _series_dir(s)
    if not os.path.isdir(path):
        return 0
    return sum(1 for f in os.listdir(path) if f.lower().endswith((".cbz", ".cbr")))


def _extract_nums(folder: str) -> set[str]:
    if not os.path.isdir(folder):
        return set()
    nums: set[str] = set()
    for f in os.listdir(folder):
        if not f.lower().endswith((".cbz", ".cbr")):
            continue
        m = re.search(r"#(\d+(?:\.\d+)?)", f)
        if m:
            try:
                nums.add(str(int(float(m.group(1)))))
            except ValueError:
                pass
    return nums


def _local_issue_numbers(s: Series) -> set[str]:
    return _extract_nums(_series_dir(s))


def _local_annual_issue_numbers(s: Series) -> set[str]:
    return _extract_nums(os.path.join(_series_dir(s), "Annuals"))


# Metron status values that mean the series will get no further issues.
_ENDED_STATUSES = {"cancelled", "completed", "ended"}
# Fallback only — used when status isn't populated yet. Intrinsically
# single-edition types.
_ENDED_SERIES_TYPES = {"Cancelled Series", "One-Shot", "Single Issue"}


def _has_upcoming_issues(s: Series, db: Session) -> bool:
    """True if the issue cache has any issue dated in the future (not yet
    released) for this series or its annual. Such a series can't be 'finished'
    yet — there's still an issue to come."""
    mids = [m for m in (s.metron_series_id, s.metron_annual_series_id) if m]
    if not mids:
        return False
    today_iso = date.today().isoformat()
    for c in db.query(MetronIssueCache).filter(MetronIssueCache.series_id.in_(mids)).all():
        d = c.store_date or c.cover_date
        if d and str(d)[:10] > today_iso:
            return True
    return False


def _series_metadata_ended(s: Series) -> bool:
    """Metron-metadata signal that a series will get no further issues."""
    if s.status:
        return s.status.strip().lower() in _ENDED_STATUSES
    if s.year_end:
        return True
    if s.series_type in _ENDED_SERIES_TYPES:
        return True
    return False


def _is_series_ended(s: Series, db: Session | None = None) -> bool:
    """True if Metron's metadata says this series will get no further issues
    AND it has no upcoming (future-dated) issues still pending.

    Primary metadata signal is `status` (Ongoing / Hiatus / Cancelled /
    Completed / Ended); falls back to year_end + intrinsically single-edition
    series_type. But even a metadata-"ended" series isn't really finished while a
    future-dated issue is still unreleased (e.g. a 12-issue maxi with #12 dated
    next month) — so when `db` is given and such an issue exists, it's not ended.
    """
    if not _series_metadata_ended(s):
        return False
    if db is not None and _has_upcoming_issues(s, db):
        return False
    return True


def _monitored_numbers(s: Series, db: Session, issue_type: str = "regular") -> set[str] | None:
    """Return the set of issue numbers the user is monitoring for the given
    issue_type, or None if the user has no explicit selection for it (regular:
    everything in [issue_min, total_issues] is implicitly monitored)."""
    rows = (
        db.query(MonitoredIssue)
        .filter(MonitoredIssue.series_id == s.id, MonitoredIssue.issue_type == issue_type)
        .all()
    )
    if not rows:
        return None
    return {r.issue_number for r in rows}


def _has_all_monitored_files(s: Series, db: Session) -> bool:
    """True if every issue the user is monitoring has a local file (both regular
    and annual issue types)."""
    # Annuals have no implicit set: only explicitly monitored annuals count.
    annual = _monitored_numbers(s, db, "annual")
    if annual is not None and not annual.issubset(_local_annual_issue_numbers(s)):
        return False

    local = _local_issue_numbers(s)
    explicit = _monitored_numbers(s, db)
    if explicit is not None:
        return explicit.issubset(local)
    # No explicit monitoring: implicit set is [issue_min, total_issues].
    if not s.total_issues:
        return False
    lo = s.issue_min or 1
    expected = {str(n) for n in range(lo, s.total_issues + 1)}
    return expected.issubset(local)


def _recompute_pause_state(s: Series, db: Session) -> None:
    """Auto-toggle s.enabled based on Metron status + monitored coverage.

    Ended in Metron + every monitored issue downloaded → pause.
    Ended in Metron but monitored issues still missing → resume.
    Not ended → leave as-is (user controls the toggle).
    """
    if not _is_series_ended(s, db):
        return
    target_enabled = not _has_all_monitored_files(s, db)
    if s.enabled != target_enabled:
        s.enabled = target_enabled
        log.info(
            "Auto-%s '%s': ended in Metron, monitored files %s",
            "paused" if not target_enabled else "resumed",
            s.series_name,
            "complete" if not target_enabled else "still missing",
        )


def _refresh_series_meta_from_metron(s: Series, db: Session) -> bool:
    """Fetch /series/{id}/ from Metron and update cover, total_issues,
    series_type, year_end, cv_id. Returns True if the call succeeded."""
    if not s.metron_series_id:
        return False
    from config import METRON_BASE_URL
    from metadata.metron_client import RateLimitedError, get as metron_get
    try:
        r = metron_get(f"{METRON_BASE_URL}/series/{s.metron_series_id}/")
        data = r.json()
    except RateLimitedError:
        raise  # let the caller stop the run instead of retrying blindly
    except Exception as exc:
        log.warning("Could not refresh series meta for %s: %s", s.series_name, exc)
        return False

    _apply_metron_series_data(s, data)
    return True


def _apply_metron_series_data(s: Series, data: dict) -> None:
    """Map a Metron /series/{id}/ detail dict onto a Series row (cover, total,
    type, status, year_end, cv_id). Shared by refresh + reading-list create."""
    img = data.get("image") or ""
    new_cover = img if isinstance(img, str) and img else None
    if new_cover:
        s.cover_image_url = new_cover
    if data.get("issue_count"):
        s.total_issues = data["issue_count"]
    st = data.get("series_type")
    if isinstance(st, dict):
        s.series_type = st.get("name") or s.series_type
    elif isinstance(st, str):
        s.series_type = st
    status = data.get("status")
    if isinstance(status, dict):
        s.status = status.get("name") or s.status
    elif isinstance(status, str):
        s.status = status
    ye = data.get("year_end")
    if ye:
        s.year_end = ye
    if not s.comicvine_volume_id and data.get("cv_id"):
        s.comicvine_volume_id = data["cv_id"]


_METRON_META_TTL = timedelta(days=7)


def _refresh_one_series(s: Series, db: Session, *, force: bool = False, skip_titles: bool = True) -> bool:
    """Unified per-series Metron refresh — the single path used by the background
    refresh job and the scheduler pre-run.

    Steps: cv_id→metron_id discovery (for txt-migrated rows), then a TTL-gated
    detail refresh (cover/total_issues/status/year_end), first-issue cover
    fallback, issue-list cache, and pause recompute. `force=True` (manual button)
    always refreshes; `force=False` (scheduler) skips series refreshed within the
    TTL to cut redundant Metron calls. Returns True if it refreshed/discovered.

    RateLimitedError propagates so the caller can stop the whole run.
    """
    from config import METRON_BASE_URL
    from metadata.metron_client import RateLimitedError, get as metron_get

    # Phase 1: discover metron_series_id from comicvine_volume_id.
    if not s.metron_series_id and s.comicvine_volume_id:
        try:
            r = metron_get(f"{METRON_BASE_URL}/series/", cv_id=s.comicvine_volume_id)
            results = r.json().get("results", [])
            if results:
                s.metron_series_id = results[0]["id"]
        except RateLimitedError:
            raise
        except Exception as exc:
            log.warning("Metron id lookup failed for %s: %s", s.series_name, exc)

    if not s.metron_series_id:
        return False

    # The detail call (cover/status/total_issues — slow-changing) is TTL-gated:
    # skipped when recently refreshed and not forced. The issue-list refresh
    # below ALWAYS runs so the scraper sees newly-released issues, and pause
    # state is ALWAYS recomputed from current coverage.
    meta_fresh = (
        not force
        and s.metron_refreshed_at is not None
        and datetime.utcnow() - s.metron_refreshed_at < _METRON_META_TTL
    )
    refreshed = False
    if not meta_fresh and _refresh_series_meta_from_metron(s, db):
        # Cover fallback: first-issue cover when the series has no image of its own.
        if not s.cover_image_url:
            try:
                r2 = metron_get(
                    f"{METRON_BASE_URL}/issue/",
                    series_id=s.metron_series_id, ordering="number", limit=1,
                )
                issues = r2.json().get("results", [])
                if issues:
                    s.cover_image_url = (_extract_img(issues[0].get("image")) or "") or None
            except RateLimitedError:
                raise
            except Exception:
                pass
        s.metron_refreshed_at = datetime.utcnow()
        refreshed = True

    for mid in filter(None, (s.metron_series_id, s.metron_annual_series_id)):
        try:
            _get_or_fetch_metron_issues(mid, db, force=True, skip_titles=skip_titles)
        except RateLimitedError:
            raise
        except Exception as exc:
            log.warning("Could not cache Metron issues for %s: %s", s.series_name, exc)

    _recompute_pause_state(s, db)
    return refreshed


def _find_issue_file(s: Series, issue_num: str, annual: bool | None = None) -> str | None:
    """Locate the local CBZ/CBR file for a given issue number.

    annual=None  → search both regular and Annuals folders (legacy behaviour)
    annual=True  → only Annuals subfolder
    annual=False → only the regular series folder
    """
    target = str(int(float(issue_num))) if issue_num else None
    if not target:
        return None
    base = _series_dir(s)
    if annual is True:
        folders = [os.path.join(base, "Annuals")]
    elif annual is False:
        folders = [base]
    else:
        folders = [base, os.path.join(base, "Annuals")]
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.lower().endswith((".cbz", ".cbr")):
                continue
            m = re.search(r"#(\d+(?:\.\d+)?)", name)
            if m and str(int(float(m.group(1)))) == target:
                return os.path.join(folder, name)
    return None


def _fetch_metron_issues(metron_series_id: int, block: bool = True) -> list[dict]:
    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get

    issues: list[dict] = []
    url = f"{METRON_BASE_URL}/issue/?series_id={metron_series_id}&ordering=number"
    while url:
        r = metron_get(url, block=block)
        data = r.json()
        issues.extend(data.get("results", []))
        url = data.get("next")
    return issues


def _build_issue_list(raw: list[dict], local_nums: set[str]) -> list[dict]:
    today = date.today()
    issues = []
    for issue in raw:
        num_str = str(issue.get("number", ""))
        try:
            normalized = str(int(float(num_str))) if num_str else ""
        except (ValueError, TypeError):
            normalized = num_str

        date_str = issue.get("store_date") or issue.get("cover_date") or ""
        issue_date = None
        if date_str:
            try:
                issue_date = date.fromisoformat(str(date_str)[:10])
            except ValueError:
                pass

        if normalized in local_nums:
            status = "downloaded"
        elif issue_date and issue_date > today:
            status = "upcoming"
        elif issue_date:
            status = "missing"
        else:
            status = "tba"

        image_raw = issue.get("image") or ""
        cover = image_raw if isinstance(image_raw, str) else (image_raw.get("medium") or "")

        # "name" field from Metron API is a list; "issue_name" is our cache key (string)
        _name = issue.get("name")
        if isinstance(_name, list):
            title = ", ".join(_name)
        else:
            title = str(issue.get("issue_name") or "")

        issues.append({
            "number": num_str,
            "title": title,
            "date": str(date_str)[:10] if date_str else "",
            "cover": cover,
            "status": status,
        })
    return issues


_ISSUE_CACHE_DAYS = 7


def _issue_sort_key(r):
    """Sort cached issues numerically (1, 2, 10), non-numeric last — mirrors the
    Metron fetch path's ordering=number so cache and fresh reads agree."""
    raw = (r.number or "").strip()
    try:
        return (0, float(raw), raw)
    except (TypeError, ValueError):
        return (1, float("inf"), raw)


def _get_or_fetch_metron_issues(
    metron_series_id: int, db: Session, *,
    force: bool = False, block: bool = True, skip_titles: bool = False,
    refresh_if_stale: bool = True,
) -> list[dict]:
    """Return issues from local cache (if fresh) else fetch from Metron and store.

    skip_titles=True skips the per-issue detail call that resolves missing
    titles — use from background jobs that only need the issue list (e.g.,
    refreshing total_issues), because that detail call is the main source of
    burst-rate-limit pressure (N calls per series).

    refresh_if_stale=False returns the cache whenever ANY rows exist (ignoring the
    TTL) and only goes to Metron when the cache is empty — the page-open path, so
    a passive view never blocks on Metron (the nightly job keeps the cache fresh).
    """
    from datetime import timedelta

    if not force:
        rows = (
            db.query(MetronIssueCache)
            .filter(MetronIssueCache.series_id == metron_series_id)
            .all()
        )
        if rows:
            # Freshness off the oldest row (a partial refresh can leave mixed ages).
            oldest = min((r.cached_at for r in rows if r.cached_at), default=None)
            fresh = bool(oldest and oldest > datetime.utcnow() - timedelta(days=_ISSUE_CACHE_DAYS))
            if fresh or not refresh_if_stale:
                rows.sort(key=_issue_sort_key)  # match the fetch path's ordering=number
                return [
                    {
                        "number": r.number,
                        "cover_date": r.cover_date,
                        "store_date": r.store_date,
                        "image": r.image_url,
                        "issue_name": r.name,
                    }
                    for r in rows
                ]

    raw = _fetch_metron_issues(metron_series_id, block=block)

    # Load existing cache entries keyed by metron_id to preserve already-fetched titles.
    existing_by_id: dict = {
        row.metron_id: row
        for row in db.query(MetronIssueCache)
        .filter(MetronIssueCache.series_id == metron_series_id)
        .all()
    }

    from config import METRON_BASE_URL
    from metadata.metron_client import RateLimitedError, get as metron_get
    now = datetime.now(timezone.utc)
    seen_ids: set = set()

    for issue in raw:
        mid = issue.get("id")
        if not mid:
            continue
        seen_ids.add(mid)

        cached_row = existing_by_id.get(mid)
        cached_name = cached_row.name if cached_row else None

        # Only call the detail endpoint for issues without a stored title.
        # Always non-blocking: skip title on rate limit rather than hanging the request.
        if not cached_name and not skip_titles:
            api_name = issue.get("name")
            if not (isinstance(api_name, list) and api_name):
                try:
                    detail = metron_get(f"{METRON_BASE_URL}/issue/{mid}/", block=False).json()
                    names = detail.get("name")
                    if isinstance(names, list) and names:
                        issue["name"] = names
                except RateLimitedError:
                    pass  # title will be fetched on next cache refresh
                except Exception as exc:
                    log.warning("Could not fetch detail for issue %s: %s", mid, exc)

        img_raw = issue.get("image") or ""
        img = img_raw if isinstance(img_raw, str) else (img_raw.get("medium") or "")
        _name = issue.get("name")
        if isinstance(_name, list) and _name:
            title = ", ".join(_name)
        else:
            title = cached_name or ""

        # Surface the resolved title on the raw dict so _build_issue_list picks it up
        issue["issue_name"] = title

        if cached_row:
            cached_row.number = str(issue.get("number", ""))
            cached_row.name = title or None
            cached_row.cover_date = str(issue.get("cover_date") or "")[:10] or None
            cached_row.store_date = str(issue.get("store_date") or "")[:10] or None
            if img:
                cached_row.image_url = img
            cached_row.cached_at = now
        else:
            db.add(MetronIssueCache(
                metron_id=mid,
                series_id=metron_series_id,
                number=str(issue.get("number", "")),
                name=title or None,
                cover_date=str(issue.get("cover_date") or "")[:10] or None,
                store_date=str(issue.get("store_date") or "")[:10] or None,
                image_url=img or None,
                cached_at=now,
            ))

    # Remove issues that no longer exist in Metron
    stale_ids = set(existing_by_id.keys()) - seen_ids
    if stale_ids:
        db.query(MetronIssueCache).filter(
            MetronIssueCache.series_id == metron_series_id,
            MetronIssueCache.metron_id.in_(stale_ids),
        ).delete(synchronize_session=False)

    # Keep Series.total_issues in sync — it's both the progress-bar denominator
    # and the scraper's issue_max upper bound (see check_and_download_comics.py).
    new_total = len(seen_ids)
    if new_total:
        (
            db.query(Series)
            .filter(Series.metron_series_id == metron_series_id)
            .update({Series.total_issues: new_total}, synchronize_session=False)
        )

    db.commit()
    return raw


# ── Metron local cache helpers ────────────────────────────────────────────────

def _extract_img(raw) -> str:
    """Normalise Metron image field — may be a URL string or a dict."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("original") or raw.get("medium") or raw.get("thumbnail") or ""
    return ""


def _ensure_cover_cached(metron_id: int, db: Session) -> str | None:
    """Return image URL from local cache; fetches from Metron on first access.

    image_url="" means tried + nothing found. image_url=None means not yet tried.
    """
    cached = db.get(MetronCache, metron_id)
    if cached is not None and cached.image_url is not None:
        return cached.image_url or None

    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get

    img = ""
    fields: dict = {}
    ok = False
    cover_lookup_complete = False
    try:
        r = metron_get(f"{METRON_BASE_URL}/series/{metron_id}/")
        data = r.json()
        img = _extract_img(data.get("image")) or ""
        pub = data.get("publisher") or {}
        st = data.get("series_type") or {}
        fields = {
            "name": data.get("name") or data.get("series"),
            "publisher_name": pub.get("name") if isinstance(pub, dict) else None,
            "year_began": data.get("year_began"),
            "issue_count": data.get("issue_count"),
            "cv_id": data.get("cv_id"),
            "series_type": st.get("name") if isinstance(st, dict) else None,
        }
        ok = True
        cover_lookup_complete = bool(img)  # primary gave an image → lookup done
        if not img:
            try:
                r2 = metron_get(
                    f"{METRON_BASE_URL}/issue/",
                    series_id=metron_id,
                    ordering="number",
                    limit=1,
                )
                issues = r2.json().get("results", [])
                cover_lookup_complete = True  # fallback completed (image or confirmed none)
                if issues:
                    img = _extract_img(issues[0].get("image")) or ""
            except Exception:
                pass  # fallback failed → cover_lookup_complete stays False (retry later)
    except Exception:
        pass

    if not ok or not cover_lookup_complete:
        # Primary fetch failed, or the cover lookup didn't complete — do NOT persist
        # image_url="" (the "tried, nothing" sentinel), or the cover would never be
        # retried. Leave it retryable.
        return None

    now = datetime.now(timezone.utc)
    if cached is None:
        db.add(MetronCache(
            metron_id=metron_id,
            image_url=img,
            cached_at=now,
            **{k: v for k, v in fields.items() if v is not None},
        ))
    else:
        cached.image_url = img
        cached.cached_at = now
        for k, v in fields.items():
            if v is not None:
                setattr(cached, k, v)
    db.commit()
    return img or None


# ── Health / JSON API ──────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/series")
def api_list_series(db: Session = Depends(get_db)):
    rows = db.query(Series).order_by(Series.publisher, Series.series_name).all()
    return [
        {
            "id": s.id,
            "publisher": s.publisher,
            "series_name": s.series_name,
            "year": s.year,
            "comicvine_volume_id": s.comicvine_volume_id,
            "metron_series_id": s.metron_series_id,
            "metron_annual_series_id": s.metron_annual_series_id,
            "annual_comicvine_volume_id": s.annual_comicvine_volume_id,
            "getcomics_search_name": s.getcomics_search_name,
            "enabled": s.enabled,
            "cover_image_url": s.cover_image_url,
            "total_issues": s.total_issues,
        }
        for s in rows
    ]


# ── Metron search proxy ────────────────────────────────────────────────────────





def _metron_search_json(name: str, db: Session) -> list[dict]:
    """Normalised Metron series search for the SPA — cache first, API fallback.
    One flat shape regardless of source: id, name, publisher, year_began,
    issue_count, series_type, image."""
    name = name.strip()
    if len(name) < 2:
        return []
    terms = name.split()
    cache_q = db.query(MetronCache)
    for term in terms:
        cache_q = cache_q.filter(MetronCache.name.ilike(f"%{term}%"))
    cache_rows = cache_q.order_by(MetronCache.year_began.desc()).limit(20).all()
    if cache_rows:
        return [
            {
                "id": r.metron_id,
                "name": r.name,
                "publisher": r.publisher_name,
                "year_began": r.year_began,
                "issue_count": r.issue_count,
                "series_type": r.series_type,
                "cv_id": r.cv_id,
                "image": r.image_url or "",
            }
            for r in cache_rows
        ]
    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get
    r = metron_get(f"{METRON_BASE_URL}/series/", name=name)
    out = []
    for s in r.json().get("results", []):
        out.append({
            "id": s.get("id"),
            "name": s.get("series") or s.get("name"),
            "publisher": (s.get("publisher") or {}).get("name") if isinstance(s.get("publisher"), dict) else s.get("publisher"),
            "year_began": s.get("year_began"),
            "issue_count": s.get("issue_count"),
            "series_type": (s.get("series_type") or {}).get("name") if isinstance(s.get("series_type"), dict) else s.get("series_type"),
            "cv_id": s.get("cv_id"),
            "image": _extract_img(s.get("image")) or "",
        })
    return out


@app.get("/api/metron/results")
def api_metron_results(name: str = "", db: Session = Depends(get_db)):
    """JSON Metron search backing the React add/edit forms."""
    try:
        return {"results": _metron_search_json(name, db)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Metron search error: {exc}")


@app.get("/api/metron/refresh/status")
def api_metron_refresh_status():
    return _metron_refresh_status_json()


@app.post("/api/metron/refresh")
def api_metron_refresh():
    """Kick the background Metron refresh (tracked series: meta, covers, issue
    lists, pause state). Returns immediately — the SPA polls the status route.
    Replaces the old synchronous /api/sync-covers + /api/metron/cache/refresh,
    which blocked the request thread (a Metron rate-limit slept it for 60s+)."""
    started = _metron_refresh.run_refresh(force=True)
    return {"started": started, **_metron_refresh_status_json()}


# ── Verify search ──────────────────────────────────────────────────────────────

def _getcomics_verify(search_term: str) -> list[dict]:
    """Live getcomics.org page-1 check — returns up to 10 {title, url}."""
    import requests as req_lib
    from bs4 import BeautifulSoup
    from config import BASE_SEARCH_URL, HEADERS

    url = f"{BASE_SEARCH_URL.format(1)}{search_term.replace(' ', '+')}"
    r = req_lib.get(url, headers=HEADERS, timeout=10)
    if r.status_code == 404 or "No Results Found" in r.text:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.select("div.post-info h1.post-title a")
    return [
        {"title": a.get_text(strip=True), "url": a["href"]}
        for a in links[:10]
        if a.get("href")
    ]




@app.get("/api/verify-search/json")
def verify_search_json(getcomics_search_name: str = "", series_name: str = ""):
    """JSON getcomics verify backing the React add/edit forms."""
    search_term = getcomics_search_name.strip() or series_name.strip()
    if not search_term:
        return {"search_term": "", "comics": []}
    try:
        return {"search_term": search_term, "comics": _getcomics_verify(search_term)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Verify error: {exc}")


# ── Download queue ────────────────────────────────────────────────────────────







def _monitor_btn(series_id: int, number: str, monitored: bool, issue_type: str = "regular") -> str:
    url = f"/series/{series_id}/issues/{number}/monitor?type={issue_type}"
    if monitored:
        return (
            f'<button class="btn btn-link btn-sm p-0 text-warning"'
            f' hx-post="{url}" hx-target="this" hx-swap="outerHTML"'
            f' title="Monitored — click to unmonitor">'
            f'<i class="bi bi-bookmark-fill"></i></button>'
        )
    return (
        f'<button class="btn btn-link btn-sm p-0 text-muted"'
        f' hx-post="{url}" hx-target="this" hx-swap="outerHTML"'
        f' title="Not monitored — click to monitor">'
        f'<i class="bi bi-bookmark"></i></button>'
    )
















# ── Downloads JSON API (React) ──────────────────────────────────────────────────


def _job_dict(j: DownloadJob, series_map: dict) -> dict:
    s = series_map.get(j.series_id)
    return {
        "id": j.id,
        "series_id": j.series_id,
        "series_name": s.series_name if s else None,
        "issue_number": j.issue_number,
        "search_term": j.search_term,
        "error": j.error,
        "filename": j.filename,
        "source": j.source,
        "status": j.status,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    }


@app.get("/api/downloads")
def api_downloads(db: Session = Depends(get_db)):
    jobs = db.query(DownloadJob).order_by(DownloadJob.created_at.desc()).limit(200).all()
    series_map = {s.id: s for s in db.query(Series).all()}
    return {"jobs": [_job_dict(j, series_map) for j in jobs]}


@app.get("/api/downloads/active")
def api_downloads_active(db: Session = Depends(get_db)):
    from web.worker import get_progress

    active = (
        db.query(DownloadJob)
        .filter(DownloadJob.status.in_(["queued", "downloading"]))
        .order_by(DownloadJob.created_at)
        .all()
    )
    series_map = {s.id: s for s in db.query(Series).all()}
    return {
        "jobs": [{**_job_dict(j, series_map), "progress": get_progress(j.id)} for j in active]
    }


@app.get("/api/downloads/badge")
def api_downloads_badge(db: Session = Depends(get_db)):
    count = (
        db.query(DownloadJob)
        .filter(DownloadJob.status.in_(["queued", "downloading"]))
        .count()
    )
    return {"count": count}


@app.delete("/api/downloads/{job_id}")
def api_download_delete(job_id: int, db: Session = Depends(get_db)):
    job = db.get(DownloadJob, job_id)
    if not job:
        raise HTTPException(status_code=404)
    if job.status in ("queued", "downloading"):
        raise HTTPException(status_code=409, detail="Cannot delete an active job.")
    db.delete(job)
    db.commit()
    return {"ok": True}


@app.post("/api/downloads/{job_id}/cancel")
def api_download_cancel(job_id: int, db: Session = Depends(get_db)):
    from web.worker import request_cancel

    job = db.get(DownloadJob, job_id)
    if not job:
        raise HTTPException(status_code=404)
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
    elif job.status == "downloading":
        request_cancel(job_id)
    return {"ok": True}


@app.delete("/api/downloads")
def api_downloads_clear(db: Session = Depends(get_db)):
    n = (
        db.query(DownloadJob)
        .filter(DownloadJob.status.in_(["done", "failed", "cancelled"]))
        .delete()
    )
    db.commit()
    return {"cleared": n}


# ── Scheduler ─────────────────────────────────────────────────────────────────









# ── Scheduler JSON API (React) ──────────────────────────────────────────────────


def _scheduler_status_json() -> dict:
    from web.scheduler import get_status
    st = get_status()
    return {
        "running": st["running"],
        "last_run_at": st["last_run_at"].isoformat() if st["last_run_at"] else None,
        "last_run_error": st["last_run_error"],
        "next_run_at": st["next_run_at"].isoformat() if st["next_run_at"] else None,
        "metron_nightly_next_run_at": (
            st["metron_nightly_next_run_at"].isoformat() if st.get("metron_nightly_next_run_at") else None
        ),
        "mode": st["mode"],
        "value": st["value"],
    }


class ScheduleConfig(BaseModel):
    mode: str
    value: str


@app.get("/api/scheduler/status")
def api_scheduler_status():
    return _scheduler_status_json()


@app.post("/api/scheduler/run")
def api_scheduler_run():
    from web.scheduler import is_running, trigger_now
    if is_running():
        return {"started": False, **_scheduler_status_json()}
    trigger_now()
    return {"started": True, **_scheduler_status_json()}


@app.post("/api/scheduler/config")
def api_scheduler_config(payload: ScheduleConfig):
    from web.scheduler import update_schedule
    try:
        update_schedule(payload.mode.strip(), payload.value.strip())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _scheduler_status_json()


# ── HTML pages ─────────────────────────────────────────────────────────────────



def _scan_series_dir(s: Series) -> tuple[int, set[str]]:
    """One listdir -> (cbz/cbr count, issue-number set). The comics volume is a
    slow NFS/mergerfs mount, so the overview scans each folder once instead of
    twice (count + number-set used to each do their own listdir)."""
    folder = _series_dir(s)
    if not os.path.isdir(folder):
        return 0, set()
    count = 0
    nums: set[str] = set()
    for f in os.listdir(folder):
        if not f.lower().endswith((".cbz", ".cbr")):
            continue
        count += 1
        m = re.search(r"#(\d+(?:\.\d+)?)", f)
        if m:
            try:
                nums.add(str(int(float(m.group(1)))))
            except ValueError:
                pass
    return count, nums


def _series_overview(db: Session):
    """Shared computation for the series grid: rows + per-series local counts,
    status classification, and footer stats. Used by both the Jinja page and
    the JSON API so the two never drift."""
    rows = db.query(Series).order_by(Series.publisher, Series.series_name).all()
    # Single filesystem scan per series → count + issue-number set (reused below
    # by has_missing_past). Halves directory round-trips on the slow comics mount.
    local_counts: dict[int, int] = {}
    local_nums: dict[int, set[str]] = {}
    for s in rows:
        local_counts[s.id], local_nums[s.id] = _scan_series_dir(s)

    # Active downloads by series_id — series that have a queued/downloading job
    active_dl_ids = {
        sid for (sid,) in db.query(DownloadJob.series_id)
        .filter(DownloadJob.status.in_(["queued", "downloading"]))
        .distinct()
        .all()
    }

    # Bulk-load cached Metron issues so we can tell "missing because past" apart
    # from "missing because the issue hasn't shipped yet". A series whose only
    # gap is a future store_date isn't actually behind — there's nothing to
    # download yet — so it should read as continuing-complete (blue), not
    # missing (red).
    metron_to_series: dict[int, list[tuple[int, str]]] = {}
    for s in rows:
        if s.metron_series_id:
            metron_to_series.setdefault(s.metron_series_id, []).append((s.id, "regular"))
        if s.metron_annual_series_id:
            metron_to_series.setdefault(s.metron_annual_series_id, []).append((s.id, "annual"))

    cached_issues_by_metron: dict[int, list] = {}
    if metron_to_series:
        for c in (
            db.query(MetronIssueCache)
            .filter(MetronIssueCache.series_id.in_(metron_to_series.keys()))
            .all()
        ):
            cached_issues_by_metron.setdefault(c.series_id, []).append(c)

    today_iso = date.today().isoformat()

    # Series with a future-dated cached issue aren't "finished" yet (reuses the
    # already-loaded cache — no extra queries).
    upcoming_ids: set[int] = set()
    for mid, sid_kinds in metron_to_series.items():
        if any((c.store_date or c.cover_date) and str(c.store_date or c.cover_date)[:10] > today_iso
               for c in cached_issues_by_metron.get(mid, [])):
            upcoming_ids.update(sid for sid, _ in sid_kinds)

    def ended_of(s: Series) -> bool:
        return _series_metadata_ended(s) and s.id not in upcoming_ids

    def has_missing_past(s: Series) -> bool:
        """True if any issue with store_date <= today (or unknown) is missing
        from the local folder. Future-only gaps don't count."""
        local_reg = local_nums[s.id]  # reuse the single scan above (no 2nd listdir)
        local_ann = _local_annual_issue_numbers(s) if s.metron_annual_series_id else set()
        for mid, kind in (
            (s.metron_series_id, "regular"),
            (s.metron_annual_series_id, "annual"),
        ):
            if not mid:
                continue
            local_set = local_ann if kind == "annual" else local_reg
            for c in cached_issues_by_metron.get(mid, []):
                if not c.number:
                    continue
                try:
                    num = str(int(float(c.number)))
                except (ValueError, TypeError):
                    num = c.number
                if num in local_set:
                    continue
                # Missing — was it supposed to be out by now? Use the same
                # date rule as upcoming_ids (store_date or cover_date); no date
                # at all → assume past (Metron often backfills dates).
                issue_date = c.store_date or c.cover_date
                if not issue_date or str(issue_date)[:10] <= today_iso:
                    return True
        return False

    # Per-series classification used for card border colour + footer stats.
    statuses: dict[int, str] = {}
    ended_map: dict[int, bool] = {}
    for s in rows:
        ended = ended_of(s)
        ended_map[s.id] = ended
        missing_past = has_missing_past(s)
        if s.id in active_dl_ids:
            statuses[s.id] = "downloading"
        elif ended and not missing_past:
            statuses[s.id] = "ended-complete"
        elif missing_past:
            statuses[s.id] = "missing-unmonitored" if not s.enabled else "missing-monitored"
        else:
            # Either everything cached is local, or what's missing hasn't shipped yet.
            statuses[s.id] = "continuing-complete"

    stats = {
        "series": len(rows),
        "ended": sum(1 for v in ended_map.values() if v),
        "continuing": sum(1 for v in ended_map.values() if not v),
        "monitored": sum(1 for s in rows if s.enabled),
        "unmonitored": sum(1 for s in rows if not s.enabled),
        "issues_total": sum((s.total_issues or 0) for s in rows),
        "files_total": sum(local_counts.values()),
    }

    return rows, local_counts, statuses, stats, ended_map




@app.get("/api/series/overview")
def api_series_overview(db: Session = Depends(get_db)):
    """JSON backing the React series grid — same data as the Jinja page."""
    rows, local_counts, statuses, stats, ended_map = _series_overview(db)
    return {
        "series": [
            {
                "id": s.id,
                "publisher": s.publisher,
                "series_name": s.series_name,
                "year": s.year,
                "cover_image_url": s.cover_image_url,
                "total_issues": s.total_issues,
                "enabled": s.enabled,
                "metron_series_id": s.metron_series_id,
                "comicvine_volume_id": s.comicvine_volume_id,
                "getcomics_search_name": s.getcomics_search_name,
                "local_count": local_counts[s.id],
                "status": statuses[s.id],
                "ended": ended_map[s.id],
            }
            for s in rows
        ],
        "stats": stats,
    }


def _series_dict(s: Series, db: Session | None = None) -> dict:
    """Editable/displayable fields for a single series (JSON API)."""
    return {
        "id": s.id,
        "publisher": s.publisher,
        "series_name": s.series_name,
        "year": s.year,
        "comicvine_volume_id": s.comicvine_volume_id,
        "metron_series_id": s.metron_series_id,
        "metron_annual_series_id": s.metron_annual_series_id,
        "annual_comicvine_volume_id": s.annual_comicvine_volume_id,
        "getcomics_search_name": s.getcomics_search_name,
        "issue_min": s.issue_min,
        "cover_image_url": s.cover_image_url,
        "total_issues": s.total_issues,
        "enabled": s.enabled,
        "ended": _is_series_ended(s, db),
    }


class SeriesUpdate(BaseModel):
    """Validated payload for editing a series (PUT /api/series/{id})."""
    publisher: str
    series_name: str
    year: int | None = None
    comicvine_volume_id: int | None = None
    metron_series_id: int | None = None
    metron_annual_series_id: int | None = None
    annual_comicvine_volume_id: int | None = None
    getcomics_search_name: str | None = None
    issue_min: int = 1

    @field_validator("publisher", "series_name")
    @classmethod
    def _nonblank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


class SeriesCreate(SeriesUpdate):
    """Validated payload for adding a series (POST /api/series)."""
    cover_image_url: str | None = None
    total_issues: int | None = None


@app.post("/api/series", status_code=201)
def api_create_series(payload: SeriesCreate, db: Session = Depends(get_db)):
    s = Series(
        publisher=payload.publisher.strip(),
        series_name=payload.series_name.strip(),
        year=payload.year,
        comicvine_volume_id=payload.comicvine_volume_id,
        metron_series_id=payload.metron_series_id,
        metron_annual_series_id=payload.metron_annual_series_id,
        annual_comicvine_volume_id=payload.annual_comicvine_volume_id,
        getcomics_search_name=(payload.getcomics_search_name or "").strip() or None,
        cover_image_url=(payload.cover_image_url or "").strip() or None,
        total_issues=payload.total_issues,
        issue_min=payload.issue_min,
    )
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A series with this publisher, name and year already exists.",
        )
    db.refresh(s)
    return _series_dict(s, db)


@app.get("/api/series/{series_id}")
def api_get_series(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    return _series_dict(s, db)


@app.put("/api/series/{series_id}")
def api_update_series(series_id: int, payload: SeriesUpdate, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    s.publisher = payload.publisher.strip()
    s.series_name = payload.series_name.strip()
    s.year = payload.year
    s.comicvine_volume_id = payload.comicvine_volume_id
    s.metron_series_id = payload.metron_series_id
    s.metron_annual_series_id = payload.metron_annual_series_id
    s.annual_comicvine_volume_id = payload.annual_comicvine_volume_id
    s.getcomics_search_name = (payload.getcomics_search_name or "").strip() or None
    s.issue_min = payload.issue_min
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A series with this publisher, name and year already exists.",
        )
    db.refresh(s)
    return _series_dict(s, db)
















# ── Bulk actions ───────────────────────────────────────────────────────────────


@app.post("/api/series/bulk/toggle")
def bulk_toggle(payload: dict, db: Session = Depends(get_db)):
    """payload = {ids: [int], action: 'pause'|'resume'}"""
    ids = payload.get("ids") or []
    action = payload.get("action")
    if action not in ("pause", "resume") or not ids:
        raise HTTPException(status_code=400, detail="Invalid payload")
    new_state = (action == "resume")
    n = (
        db.query(Series).filter(Series.id.in_(ids))
        .update({Series.enabled: new_state}, synchronize_session=False)
    )
    db.commit()
    return {"updated": n}


@app.post("/api/series/bulk/monitor")
def bulk_monitor(payload: dict, db: Session = Depends(get_db)):
    """payload = {ids: [int], mode: 'all'|'none'|'future'|'missing'}.

    all     → mark every cached issue as monitored
    none    → clear all monitoring rows for these series
    future  → monitor issues with store_date >= today
    missing → monitor issues that exist in Metron but not locally
    """
    ids = payload.get("ids") or []
    mode = payload.get("mode")
    if mode not in ("all", "none", "future", "missing") or not ids:
        raise HTTPException(status_code=400, detail="Invalid payload")

    rows = db.query(Series).filter(Series.id.in_(ids)).all()
    today_iso = date.today().isoformat()

    for s in rows:
        # Always clear first — every mode is an absolute statement.
        db.query(MonitoredIssue).filter(MonitoredIssue.series_id == s.id).delete()
        if mode == "none":
            continue
        for mid, itype in (
            (s.metron_series_id, "regular"),
            (s.metron_annual_series_id, "annual"),
        ):
            if not mid:
                continue
            cached = (
                db.query(MetronIssueCache)
                .filter(MetronIssueCache.series_id == mid)
                .all()
            )
            local_nums = (
                _local_annual_issue_numbers(s) if itype == "annual"
                else _local_issue_numbers(s)
            )
            for c in cached:
                if not c.number:
                    continue
                try:
                    norm = str(int(float(c.number)))
                except (ValueError, TypeError):
                    norm = c.number
                if mode == "future":
                    if not c.store_date or c.store_date < today_iso:
                        continue
                elif mode == "missing":
                    if norm in local_nums:
                        continue
                db.add(MonitoredIssue(series_id=s.id, issue_number=norm, issue_type=itype))
        _recompute_pause_state(s, db)
    db.commit()
    return {"updated": len(rows)}


@app.post("/api/series/bulk/refresh")
def bulk_refresh(payload: dict, db: Session = Depends(get_db)):
    """Refresh cover + total_issues + ended status from Metron for each id."""
    ids = payload.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="Invalid payload")
    rows = (
        db.query(Series)
        .filter(Series.id.in_(ids))
        .filter(Series.metron_series_id.isnot(None))
        .all()
    )
    refreshed = 0
    for s in rows:
        if _refresh_series_meta_from_metron(s, db):
            _recompute_pause_state(s, db)
            refreshed += 1
    db.commit()
    return {"updated": refreshed, "skipped": len(ids) - refreshed}


@app.post("/api/series/bulk/delete")
def bulk_delete(payload: dict, db: Session = Depends(get_db)):
    ids = payload.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="Invalid payload")
    n = db.query(Series).filter(Series.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": n}






# ── Series detail JSON API (React) ──────────────────────────────────────────────


@app.get("/api/series/{series_id}/detail")
def api_series_detail(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    return {**_series_dict(s, db), "local_count": _count_local_issues(s)}


@app.get("/api/series/{series_id}/issues")
def api_series_issues(series_id: int, force: bool = False, db: Session = Depends(get_db)):
    """JSON issues list (regular + annual) for the React detail page."""
    from metadata.metron_client import RateLimitedError

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    if not s.metron_series_id:
        return {"has_metron": False, "regular": [], "annual": []}

    local_nums = _local_issue_numbers(s)
    local_annual_nums = _local_annual_issue_numbers(s) if s.metron_annual_series_id else set()
    try:
        raw = _get_or_fetch_metron_issues(s.metron_series_id, db, force=force, block=False,
                                          refresh_if_stale=False, skip_titles=not force)
        regular = _build_issue_list(raw, local_nums)
    except RateLimitedError as exc:
        return {"has_metron": True, "rate_limited": int(exc.seconds) + 2, "regular": [], "annual": []}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Metron error: {exc}")

    annual: list[dict] = []
    if s.metron_annual_series_id:
        try:
            annual = _build_issue_list(
                _get_or_fetch_metron_issues(s.metron_annual_series_id, db, force=force, block=False,
                                            refresh_if_stale=False, skip_titles=not force),
                local_annual_nums,
            )
        except Exception:
            pass

    first = (
        db.query(MetronIssueCache).filter(MetronIssueCache.series_id == s.metron_series_id).first()
    )
    rows = db.query(MonitoredIssue).filter(MonitoredIssue.series_id == s.id).all()
    return {
        "has_metron": True,
        "regular": regular,
        "annual": annual,
        "monitored_regular": sorted({m.issue_number for m in rows if m.issue_type == "regular"}),
        "monitored_annual": sorted({m.issue_number for m in rows if m.issue_type == "annual"}),
        "has_monitoring": bool(rows),
        "cached_at": first.cached_at.isoformat() if first and first.cached_at else None,
    }


@app.post("/api/series/{series_id}/issues/{number}/monitor")
def api_issue_monitor(series_id: int, number: str, type: str = Query(default="regular"), db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    issue_type = type if type in ("regular", "annual") else "regular"
    try:
        norm = str(int(float(number)))
    except (ValueError, TypeError):
        norm = number
    existing = (
        db.query(MonitoredIssue)
        .filter(
            MonitoredIssue.series_id == series_id,
            MonitoredIssue.issue_number == norm,
            MonitoredIssue.issue_type == issue_type,
        )
        .first()
    )
    if existing:
        db.delete(existing)
        monitored = False
    else:
        db.add(MonitoredIssue(series_id=series_id, issue_number=norm, issue_type=issue_type))
        monitored = True
    _recompute_pause_state(s, db)
    db.commit()
    return {"monitored": monitored}


@app.post("/api/series/{series_id}/monitor-all")
def api_monitor_all(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    def _insert_type(metron_series_id: int, issue_type: str) -> None:
        rows = db.query(MetronIssueCache).filter(MetronIssueCache.series_id == metron_series_id).all()
        existing = {
            m.issue_number
            for m in db.query(MonitoredIssue)
                .filter(MonitoredIssue.series_id == series_id, MonitoredIssue.issue_type == issue_type)
                .all()
        }
        for row in rows:
            if not row.number:
                continue
            try:
                norm = str(int(float(row.number)))
            except (ValueError, TypeError):
                norm = row.number
            if norm not in existing:
                db.add(MonitoredIssue(series_id=series_id, issue_number=norm, issue_type=issue_type))
                existing.add(norm)

    if s.metron_series_id:
        _insert_type(s.metron_series_id, "regular")
    if s.metron_annual_series_id:
        _insert_type(s.metron_annual_series_id, "annual")
    _recompute_pause_state(s, db)
    db.commit()
    return {"ok": True}


@app.delete("/api/series/{series_id}/monitor-all")
def api_unmonitor_all(series_id: int, db: Session = Depends(get_db)):
    db.query(MonitoredIssue).filter(MonitoredIssue.series_id == series_id).delete()
    s = db.query(Series).filter(Series.id == series_id).first()
    if s:
        _recompute_pause_state(s, db)
    db.commit()
    return {"ok": True}


@app.post("/api/series/{series_id}/issues/{number}/download")
def api_issue_download(
    series_id: int, number: str,
    url: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    existing = (
        db.query(DownloadJob)
        .filter(
            DownloadJob.series_id == series_id,
            DownloadJob.issue_number == number,
            DownloadJob.status.in_(["queued", "downloading"]),
        )
        .first()
    )
    # url (from the Releases feed) lets the worker download directly without
    # re-searching getcomics. The worker fetches it server-side, so allowlist
    # getcomics.org to prevent SSRF; reject anything else outright.
    if url:
        from util import is_getcomics_url
        if not is_getcomics_url(url):
            raise HTTPException(status_code=400, detail="url must be a getcomics.org link")
    if not existing:
        search_name = s.getcomics_search_name or s.series_name
        job = DownloadJob(
            series_id=series_id,
            issue_number=number,
            search_term=f"{search_name} #{number} ({s.year})",
            url=url or None,
            status="queued",
        )
        db.add(job)
        db.commit()
        from web import worker
        worker.enqueue(job.id)
    return {"status": "queued"}


@app.delete("/api/series/{series_id}/issues/{issue_num}")
def api_issue_delete(series_id: int, issue_num: str, type: str | None = Query(default=None), db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    ok, msg = _delete_issue_file(s, issue_num, type)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    return {"ok": True, "message": msg}


@app.post("/api/series/{series_id}/scan")
def api_series_scan(series_id: int, force: bool = False, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    _scanner.run_scan([s.to_scraper_tuple()], force=force)
    return {"ok": True}


@app.delete("/api/series/{series_id}")
def api_series_delete(series_id: int, db: Session = Depends(get_db)):
    n = db.query(Series).filter(Series.id == series_id).delete(synchronize_session=False)
    db.commit()
    if not n:
        raise HTTPException(status_code=404)
    return {"deleted": n}


@app.get("/api/series/{series_id}/issues/{issue_num}/metadata")
def api_issue_metadata(series_id: int, issue_num: str, source: str = "", db: Session = Depends(get_db)):
    """ComicInfo fields for an issue. source=metron → fetch defaults from Metron."""
    from metadata.comicinfo_io import read_comicinfo, empty_fields

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    cbz = _find_issue_file(s, issue_num)
    if source == "metron":
        from metadata.get_comic_metadata import get_comic_metadata

        fields = empty_fields()
        try:
            meta = get_comic_metadata(s.to_scraper_tuple(), issue_num)
            if meta:
                fields["Series"] = str(meta.get("series_name") or s.series_name or "")
                fields["Number"] = str(meta.get("issue_number") or issue_num)
                fields["Title"] = str(meta.get("title") or "")
                fields["Publisher"] = str(meta.get("publisher") or s.publisher or "")
                fields["Summary"] = str(meta.get("description") or "")
                fields["PageCount"] = str(meta.get("page_count") or "")
                store_date = meta.get("store_date") or ""
                if len(store_date) >= 7:
                    fields["Year"] = store_date[:4]
                    fields["Month"] = str(int(store_date[5:7]))
                for role in ("Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist"):
                    key = role.lower() if role != "CoverArtist" else "cover_artist"
                    v = meta.get(key) or meta.get(role) or ""
                    if isinstance(v, list):
                        v = ", ".join(str(x) for x in v)
                    fields[role] = str(v)
        except Exception as exc:
            log.error("Failed to fetch from Metron: %s", exc)
        return {"fields": fields, "filename": os.path.basename(cbz) if cbz else "", "from_metron": True}
    if not cbz:
        raise HTTPException(status_code=404, detail="Local file not found for this issue.")
    try:
        fields = read_comicinfo(cbz)
    except Exception as exc:
        log.error("Failed to read ComicInfo.xml from %s: %s", cbz, exc)
        fields = empty_fields()
    return {"fields": fields, "filename": os.path.basename(cbz), "from_metron": False}


@app.post("/api/series/{series_id}/issues/{issue_num}/metadata")
def api_issue_metadata_save(series_id: int, issue_num: str, payload: dict, db: Session = Depends(get_db)):
    from metadata.comicinfo_io import write_comicinfo, EDITOR_FIELDS

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    cbz = _find_issue_file(s, issue_num)
    if not cbz:
        raise HTTPException(status_code=404, detail="Local file not found.")
    fields = {k: str(payload.get(k) or "").strip() for k in EDITOR_FIELDS}
    try:
        ok = write_comicinfo(cbz, fields)
    except Exception as exc:
        log.error("Failed to write ComicInfo.xml to %s: %s", cbz, exc)
        raise HTTPException(status_code=500, detail=f"Save failed: {exc}")
    if not ok:
        raise HTTPException(status_code=500, detail="Save returned failure.")
    return {"ok": True}


@app.get("/api/series/{series_id}/series-xml")
def api_series_xml(series_id: int, db: Session = Depends(get_db)):
    from metadata.series_xml import read_series_xml

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    return {"fields": read_series_xml(_series_dir(s))}


@app.post("/api/series/{series_id}/series-xml")
def api_series_xml_save(series_id: int, payload: dict, db: Session = Depends(get_db)):
    from metadata.series_xml import FIELDS, write_series_xml

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    fields = {k: str(payload.get(k) or "").strip() for k in FIELDS}
    try:
        write_series_xml(_series_dir(s), fields)
    except Exception as exc:
        log.error("Failed to write series.xml: %s", exc)
        raise HTTPException(status_code=500, detail=f"Save failed: {exc}")
    return {"ok": True}


@app.get("/api/series/{series_id}/rename-preview")
def api_rename_preview(series_id: int, db: Session = Depends(get_db)):
    from retag_comics import _issue_number as _parse_num, expected_filename

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    entry = s.to_scraper_tuple()

    def _scan(directory: str, scan_entry: tuple) -> list[dict]:
        if not os.path.isdir(directory):
            return []
        out = []
        for filename in sorted(os.listdir(directory)):
            if not filename.lower().endswith((".cbz", ".cbr")):
                continue
            ext = os.path.splitext(filename)[1].lower()
            num = _parse_num(filename)
            exp = expected_filename(scan_entry, num, ext) if num else None
            out.append({
                "folder": directory,
                "current": filename,
                "expected": exp,
                "changed": exp is not None and filename != exp,
            })
        return out

    series_dir = _series_dir(s)
    items = _scan(series_dir, entry)
    items += _scan(os.path.join(series_dir, "Annuals"), (entry[0], f"{entry[1]} Annual", entry[2]))
    return {
        "changed": [i for i in items if i["changed"]],
        "correct_count": sum(1 for i in items if i["expected"] and not i["changed"]),
        "unparseable": [i for i in items if i["expected"] is None],
    }


@app.post("/api/series/{series_id}/rename-apply")
def api_rename_apply(series_id: int, payload: dict, db: Session = Depends(get_db)):
    renames = payload.get("renames") or []
    base = os.path.realpath(COMICS_BASE_DIR)
    renamed = errors = 0
    for item in renames:
        folder = str(item.get("folder", ""))
        current = str(item.get("current", ""))
        expected = str(item.get("expected", ""))
        if not (folder and current and expected):
            errors += 1
            continue
        src = os.path.join(folder, current)
        dst = os.path.join(folder, expected)
        try:
            # commonpath raises ValueError on Windows for cross-drive paths
            escapes = (
                os.path.commonpath([base, os.path.realpath(src)]) != base
                or os.path.commonpath([base, os.path.realpath(dst)]) != base
            )
            if escapes:
                log.warning("Rename: path escapes comics dir: %s -> %s", src, dst)
                errors += 1
            elif not os.path.exists(src) or os.path.exists(dst):
                errors += 1
            else:
                os.rename(src, dst)
                renamed += 1
        except (OSError, ValueError) as exc:
            log.error("Rename error: %s", exc)
            errors += 1
    return {"renamed": renamed, "errors": errors}


# ── Library scan ───────────────────────────────────────────────────────────────

from web import scanner as _scanner
from web import metron_refresh as _metron_refresh


def _metron_refresh_status_json() -> dict:
    st = _metron_refresh.get_status()
    last = st.get("last_refresh_at")
    return {
        "running": st.get("running", False),
        "last_refresh_at": last.isoformat() if last else None,
        "last_error": st.get("last_error"),
        "progress": st.get("progress") or {"current": "", "done": 0, "total": 0},
        "last_result": st.get("last_result") or {},
    }






def _scan_status_json() -> dict:
    st = _scanner.get_status()
    last = st.get("last_scan_at")
    return {
        "running": st.get("running", False),
        "last_scan_at": last.isoformat() if last else None,
        "last_scan_error": st.get("last_scan_error"),
        "progress": st.get("progress") or {"current": "", "done": 0, "total": 0},
    }


@app.get("/api/library/status")
def api_library_status():
    return _scan_status_json()


@app.post("/api/library/scan")
def api_library_scan(force: bool = Query(default=False)):
    from retag_comics import load_series_from_db
    started = _scanner.run_scan(load_series_from_db(), force=force)
    return {"started": started, **_scan_status_json()}




# ── Issue file deletion ────────────────────────────────────────────────────────


def _delete_issue_file(s: Series, issue_num: str, issue_type: str | None) -> tuple[bool, str]:
    """Remove a single issue file from disk. Returns (ok, message)."""
    annual = True if (issue_type or "").lower() == "annual" else False if issue_type else None
    path = _find_issue_file(s, issue_num, annual=annual)
    if not path:
        return False, f"#{issue_num}: local file not found"
    try:
        os.remove(path)
    except OSError as exc:
        log.error("Failed to delete issue file %s: %s", path, exc)
        return False, f"#{issue_num}: {exc}"
    return True, f"#{issue_num} deleted"




@app.post("/api/series/{series_id}/issues/bulk/delete")
def issue_bulk_delete(series_id: int, payload: dict, db: Session = Depends(get_db)):
    items = payload.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="Invalid payload")
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    deleted = 0
    errors: list[str] = []
    for item in items:
        num = str(item.get("number", "")).strip()
        if not num:
            continue
        ok, msg = _delete_issue_file(s, num, item.get("type"))
        if ok:
            deleted += 1
        else:
            errors.append(msg)
    return {"deleted": deleted, "errors": errors}


# ── Local metadata editor ──────────────────────────────────────────────────────
















# ── Log viewer ─────────────────────────────────────────────────────────────────

LOG_DIR = os.getenv("LOG_DIR", "logs")
_LOG_LINES_DEFAULT = 200


def _log_files() -> list[dict]:
    """Return log files sorted newest first with size info."""
    if not os.path.isdir(LOG_DIR):
        return []
    files = []
    for name in os.listdir(LOG_DIR):
        if not name.endswith(".log"):
            continue
        path = os.path.join(LOG_DIR, name)
        stat = os.stat(path)
        files.append({
            "name": name,
            "path": path,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def _current_log_path() -> str | None:
    """Return the path of the active FileHandler log, or newest file."""
    import logging as _logging
    for h in _logging.getLogger().handlers:
        if isinstance(h, _logging.FileHandler):
            return h.baseFilename
    files = _log_files()
    return files[0]["path"] if files else None


def _read_tail(path: str, n: int) -> list[str]:
    """Read last n lines of a file efficiently."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            buf = bytearray()
            pos = size
            lines_found = 0
            chunk = 8192
            while pos > 0 and lines_found < n + 1:
                read_size = min(chunk, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size)
                buf = bytearray(data) + buf
                lines_found = buf.count(b"\n")
            text = buf.decode("utf-8", errors="replace")
            return text.splitlines()[-n:]
    except OSError:
        return []


def _cleanup_old_logs(retention_days: int) -> int:
    """Delete log files older than retention_days. Returns count deleted."""
    import time
    cutoff = time.time() - retention_days * 86400
    deleted = 0
    active = _current_log_path()
    for f in _log_files():
        if f["mtime"] < cutoff and f["path"] != active:
            try:
                os.remove(f["path"])
                deleted += 1
            except OSError:
                pass
    return deleted


def _get_setting(db, key: str, default: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else default


def _set_setting(db, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


# Back-compat aliases (the log routes were the first users of AppSetting).
_get_log_setting = _get_setting
_set_log_setting = _set_setting


# ── Calendar ───────────────────────────────────────────────────────────────────


def _calendar_range(view: str, ref: date) -> tuple[date, date]:
    """Return (start, end) inclusive for the calendar grid containing ref.

    Week view: Monday → Sunday of ref's week.
    Month view: starts on the Monday of the week that contains day 1; ends
    on the Sunday of the week that contains the last day. Always a multiple
    of 7 days so the grid renders as full rows.
    """
    if view == "week":
        start = ref - timedelta(days=ref.weekday())
        return start, start + timedelta(days=6)
    first = ref.replace(day=1)
    last = date(ref.year, ref.month, monthrange(ref.year, ref.month)[1])
    start = first - timedelta(days=first.weekday())
    end = last + timedelta(days=(6 - last.weekday()))
    return start, end


def _calendar_shift(view: str, ref: date, direction: int) -> date:
    """direction = -1 or +1; week shifts by 7 days, month by one calendar month."""
    if view == "week":
        return ref + timedelta(days=7 * direction)
    if direction < 0:
        return (ref.replace(day=1) - timedelta(days=1)).replace(day=1)
    year, month = ref.year, ref.month + 1
    if month > 12:
        year, month = year + 1, 1
    return date(year, month, 1)


def _load_calendar_events(
    db: Session, start: date, end: date
) -> dict[str, list[dict]]:
    """Return {YYYY-MM-DD: [event, ...]} for enabled series in [start, end].

    Uses MetronIssueCache.store_date (in-store date) — explicit user choice
    over cover_date because the latter often drifts a month from release.
    """
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    rows = (
        db.query(MetronIssueCache, Series)
        .join(
            Series,
            or_(
                MetronIssueCache.series_id == Series.metron_series_id,
                MetronIssueCache.series_id == Series.metron_annual_series_id,
            ),
        )
        .filter(Series.enabled == True)  # noqa: E712
        .filter(MetronIssueCache.store_date.isnot(None))
        .filter(MetronIssueCache.store_date != "")
        .filter(MetronIssueCache.store_date >= start_iso)
        .filter(MetronIssueCache.store_date <= end_iso)
        .all()
    )

    # Cache local issue numbers per series once instead of scanning the
    # folder for every event.
    local_cache: dict[tuple[int, bool], set[str]] = {}
    today = date.today()
    events: dict[str, list[dict]] = {}

    for issue, s in rows:
        is_annual = bool(
            s.metron_annual_series_id and issue.series_id == s.metron_annual_series_id
        )
        key = (s.id, is_annual)
        if key not in local_cache:
            local_cache[key] = (
                _local_annual_issue_numbers(s) if is_annual
                else _local_issue_numbers(s)
            )
        local_nums = local_cache[key]

        num_raw = (issue.number or "").strip()
        try:
            num_norm = str(int(float(num_raw))) if num_raw else ""
        except (ValueError, TypeError):
            num_norm = num_raw
        downloaded = bool(num_norm) and num_norm in local_nums

        try:
            ev_date = date.fromisoformat(issue.store_date)
        except ValueError:
            continue

        if downloaded:
            status = "downloaded"
        elif ev_date == today:
            status = "today"
        elif ev_date < today:
            status = "missing"
        else:
            status = "upcoming"

        events.setdefault(issue.store_date, []).append({
            "series_id": s.id,
            "series_name": s.series_name + (" Annual" if is_annual else ""),
            "issue_number": num_raw,
            "issue_name": issue.name or "",
            "status": status,
            "is_annual": is_annual,
        })

    for day_list in events.values():
        day_list.sort(key=lambda e: (e["series_name"].lower(), e["issue_number"]))
    return events




@app.get("/api/calendar")
def api_calendar(
    view: str = "month",
    date_str: str = Query("", alias="date"),
    db: Session = Depends(get_db),
):
    """JSON calendar grid (weeks of day cells with events) for the React page."""
    if view not in ("month", "week"):
        view = "month"
    try:
        ref = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        ref = date.today()

    start, end = _calendar_range(view, ref)
    events = _load_calendar_events(db, start, end)

    weeks: list[list[dict]] = []
    cursor = start
    today = date.today()
    while cursor <= end:
        week: list[dict] = []
        for _ in range(7):
            iso = cursor.isoformat()
            week.append({
                "iso": iso,
                "day": cursor.day,
                "is_today": cursor == today,
                "in_view_month": (view == "week") or cursor.month == ref.month,
                "events": events.get(iso, []),
            })
            cursor += timedelta(days=1)
        weeks.append(week)

    if view == "week":
        header_label = f"{start.strftime('%b %d')} — {end.strftime('%b %d, %Y')}"
    else:
        header_label = ref.strftime("%B %Y")

    return {
        "view": view,
        "weeks": weeks,
        "header_label": header_label,
        "prev_ref": _calendar_shift(view, ref, -1).isoformat(),
        "next_ref": _calendar_shift(view, ref, +1).isoformat(),
        "today_iso": date.today().isoformat(),
        "current_ref": ref.isoformat(),
    }


# ── Releases (RSS feed) ────────────────────────────────────────────────────────


def _match_feed_entries(entries, db: Session) -> list[dict]:
    """Map RSS feed entries to monitored series + tag them with current state.

    For each entry whose title parses as 'Series #N (YYYY)', try to find a
    series whose normalized name matches. Returns a list ordered by feed
    pub_date desc, each item describing whether we already have it locally
    or have a job in flight.
    """
    from util import normalize_title, norm_issue_number

    series_rows = db.query(Series).filter(Series.enabled == True).all()  # noqa: E712
    # Index by normalized name for O(1) match.
    by_norm: dict[str, Series] = {}
    for s in series_rows:
        # Same year-stripping the scraper does — series_name may carry "(YYYY)"
        # for disambiguation, getcomics titles don't.
        norm = re.sub(
            r"\s*\(\d{4}\)\s*$", "", normalize_title(s.series_name)
        ).strip()
        if norm:
            by_norm[norm] = s

    # Precompute local file numbers per series (only once each).
    local_cache: dict[int, set[str]] = {}

    # In-flight downloads per (series_id, issue_number).
    in_flight: set[tuple[int, str]] = set()
    for j in (
        db.query(DownloadJob)
        .filter(DownloadJob.status.in_(["queued", "downloading"]))
        .all()
    ):
        in_flight.add((j.series_id, norm_issue_number(j.issue_number)))

    results: list[dict] = []
    for e in entries:
        if not e.series_name:
            continue
        norm = normalize_title(e.series_name)
        s = by_norm.get(norm)
        if not s:
            continue
        num_norm = norm_issue_number(e.issue_number)

        if s.id not in local_cache:
            local_cache[s.id] = _local_issue_numbers(s)
        downloaded = num_norm in local_cache[s.id]
        queued = (s.id, num_norm) in in_flight

        results.append({
            "entry": e,
            "series": s,
            "issue_number": num_norm,
            "downloaded": downloaded,
            "queued": queued,
        })

    return results






@app.get("/api/releases")
def api_releases(db: Session = Depends(get_db)):
    """JSON getcomics RSS matches against monitored series (React Releases page)."""
    from comic_search.rss_feed import fetch_feed

    try:
        entries = fetch_feed()
        matches = _match_feed_entries(entries, db)
    except Exception as exc:
        log.warning("Failed to fetch RSS feed: %s", exc)
        return {"matches": [], "feed_size": 0, "error": str(exc)}

    return {
        "feed_size": len(entries),
        "error": None,
        "matches": [
            {
                "series_id": m["series"].id,
                "series_name": m["series"].series_name,
                "cover_image_url": m["series"].cover_image_url,
                "issue_number": m["issue_number"],
                "title": m["entry"].title,
                "url": m["entry"].url,
                "pub_date": m["entry"].pub_date.isoformat() if m["entry"].pub_date else None,
                "downloaded": m["downloaded"],
                "queued": m["queued"],
            }
            for m in matches
        ],
    }
















# ── Log viewer — JSON endpoints (SPA) ────────────────────────────────────────


def _classify_log_line(line: str) -> str:
    """Mirror partials/log_stream.html line-class logic. Single source of truth
    for both the Jinja template and the SPA terminal colours."""
    if " - ERROR - " in line:
        return "error"
    if " - WARNING - " in line:
        return "warning"
    if "Tagged " in line or "Writing metadata" in line or "Downloading" in line:
        return "meta"
    low = line.lower()
    if "download" in low or "cbz" in low:
        return "dl"
    return "info"


class LogSettings(BaseModel):
    log_retention_days: int


@app.get("/api/logs")
def api_logs(db: Session = Depends(get_db)):
    files = _log_files()
    current = _current_log_path()
    current_name = os.path.basename(current) if current else (files[0]["name"] if files else "")
    return {
        "files": [{"name": f["name"], "size": f["size"]} for f in files],
        "current_name": current_name,
        "retention_days": int(_get_log_setting(db, "log_retention_days", "7")),
        "lines_default": _LOG_LINES_DEFAULT,
    }


@app.get("/api/logs/files")
def api_logs_files():
    return {"files": [{"name": f["name"], "size": f["size"]} for f in _log_files()]}


@app.get("/api/logs/stream")
def api_logs_stream(filename: str = "", lines: int = _LOG_LINES_DEFAULT, level: str = ""):
    lines = min(max(1, lines), 10000)  # cap so a client can't request unbounded tail into memory
    path = os.path.join(LOG_DIR, os.path.basename(filename)) if filename else _current_log_path()
    if not path or not os.path.isfile(path):
        return {"filename": filename, "lines": []}
    raw = _read_tail(path, lines)
    if level:
        raw = [l for l in raw if f" - {level.upper()} - " in l]
    return {
        "filename": os.path.basename(path),
        "lines": [{"text": l, "cls": _classify_log_line(l)} for l in raw],
    }


@app.get("/api/logs/{filename}/download")
def api_log_download(filename: str):
    safe = os.path.basename(filename)
    path = os.path.join(LOG_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="text/plain", filename=safe)


@app.delete("/api/logs/{filename}")
def api_log_delete(filename: str):
    safe = os.path.basename(filename)
    path = os.path.join(LOG_DIR, safe)
    if os.path.normpath(path) == os.path.normpath(_current_log_path() or ""):
        raise HTTPException(status_code=409, detail="Cannot delete the active log file.")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    try:
        os.remove(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"deleted": 1}


@app.post("/api/logs/cleanup")
def api_log_cleanup(db: Session = Depends(get_db)):
    retention = int(_get_log_setting(db, "log_retention_days", "7"))
    return {"deleted": _cleanup_old_logs(retention)}


@app.post("/api/logs/settings")
def api_log_settings(payload: LogSettings, db: Session = Depends(get_db)):
    days = max(1, payload.log_retention_days)
    _set_log_setting(db, "log_retention_days", str(days))
    return {"retention_days": days}


# ── Reading lists ────────────────────────────────────────────────────────────

def _norm_issue_num(n) -> str:
    """Match the scraper/monitoring normalisation ('001' == '1' == '1.0')."""
    try:
        return str(int(float(n)))
    except (ValueError, TypeError):
        return str(n or "").strip()


# In-memory TTL cache for reading-list SEARCH only — the added lists are the
# durable backup. ponytail: lost on restart, fine for search.
_RL_SEARCH_CACHE: dict[tuple, tuple[float, list]] = {}
_RL_SEARCH_TTL = 3600  # seconds

# monitored_issue_types storage: "" = monitor all (also the legacy default),
# a sentinel = monitor none, else a CSV of issue_types. The sentinel keeps the
# all-vs-none distinction across a re-sync round-trip (can't be a real type).
_RL_MONITOR_NONE = "\x00none"


def _serialize_monitor_types(types: list[str] | None) -> str:
    if types is None:
        return ""              # monitor all
    if not types:
        return _RL_MONITOR_NONE  # monitor none (explicit empty choice)
    return ",".join(types)


def _deserialize_monitor_types(value: str | None) -> list[str] | None:
    if value == _RL_MONITOR_NONE:
        return []              # monitor none
    if not value:
        return None            # monitor all (incl. legacy "" rows)
    return [t for t in value.split(",") if t]


def _create_or_get_series_from_metron(metron_series_id: int, db: Session) -> Series | None:
    """Find a local Series by metron_series_id, or create it from Metron detail."""
    s = db.query(Series).filter(Series.metron_series_id == metron_series_id).first()
    if s:
        return s
    from config import METRON_BASE_URL
    from metadata.metron_client import RateLimitedError, get as metron_get
    try:
        data = metron_get(f"{METRON_BASE_URL}/series/{metron_series_id}/", block=False).json()
    except RateLimitedError:
        raise
    except Exception as exc:
        log.warning("Could not fetch Metron series %s: %s", metron_series_id, exc)
        return None
    publisher = (data.get("publisher") or {}).get("name") or "Unknown"
    name = data.get("name") or data.get("series") or f"Series {metron_series_id}"
    year = data.get("year_began")
    s = Series(publisher=publisher, series_name=name, year=year, metron_series_id=metron_series_id)
    _apply_metron_series_data(s, data)  # cover / total / status / cv_id (no extra call)
    # Insert in a SAVEPOINT so a unique-constraint clash doesn't roll back the
    # caller's in-progress reading-list transaction.
    try:
        with db.begin_nested():
            db.add(s)
            db.flush()
        return s
    except IntegrityError:
        # A series with the same (publisher, name, year) already exists — reuse it.
        s = (
            db.query(Series)
            .filter(Series.publisher == publisher, Series.series_name == name, Series.year == year)
            .first()
        )
        if s and not s.metron_series_id:
            s.metron_series_id = metron_series_id
        return s


def _find_local_series(db: Session, name: str | None, year: int | None) -> Series | None:
    """Match a tracked Series by name (case-insensitive) + year — the join key
    for reading-list items, whose Metron payload has no series id."""
    if not name:
        return None
    q = db.query(Series).filter(Series.series_name.ilike(name.strip()))
    # When a year is known, require it — falling back to any same-named series
    # could match the wrong volume.
    if year is not None:
        return q.filter(Series.year == year).first()
    return q.first()


def _resolve_series_for_reading_list(
    db: Session, name: str | None, year: int | None, volume: int | None = None,
) -> Series | None:
    """Find the local Series for a reading-list item by name + year. If none is
    tracked, resolve the Metron series id via search (matching year_began/volume)
    and create it. Never blocks the add on Metron — returns None on any failure."""
    s = _find_local_series(db, name, year)
    if s or not name:
        return s
    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get
    try:
        results = metron_get(f"{METRON_BASE_URL}/series/", name=name, block=False).json().get("results", [])
    except Exception as exc:  # incl. RateLimitedError — don't fail the whole add
        log.warning("Metron series search failed for %r: %s", name, exc)
        return None
    match = next(
        (r for r in results if r.get("year_began") == year and (volume is None or r.get("volume") == volume)),
        None,
    ) or next((r for r in results if r.get("year_began") == year), None)
    if not match:
        return None
    try:
        return _create_or_get_series_from_metron(match["id"], db)
    except Exception as exc:
        log.warning("Could not create series %r from Metron: %s", name, exc)
        return None


def _reading_list_dict(rl: ReadingList, db: Session) -> dict:
    items = db.query(ReadingListItem).filter(ReadingListItem.reading_list_id == rl.id).all()
    owned = sum(1 for it in items if _item_status(it, db) == "owned")
    return {
        "id": rl.id,
        "metron_id": rl.metron_id,
        "name": rl.name,
        "list_type": rl.list_type,
        "attribution_source": rl.attribution_source,
        "image_url": rl.image_url,
        "average_rating": rl.average_rating,
        "num_items": rl.num_items,
        "monitored_issue_types": _deserialize_monitor_types(rl.monitored_issue_types),
        "owned": owned,
        "total": len(items),
        "synced_at": rl.synced_at.isoformat() if rl.synced_at else None,
    }


# series_id -> set of local issue numbers, memoised per request to avoid
# re-scanning the same folder for every item in a list.
def _item_status(it: ReadingListItem, db: Session, _local_cache: dict | None = None) -> str:
    if not it.series_id:
        return "untracked"
    s = db.get(Series, it.series_id)
    if not s:
        return "untracked"
    num = _norm_issue_num(it.number)
    local = (_local_cache or {}).get(it.series_id)
    if local is None:
        local = _local_issue_numbers(s)
        if _local_cache is not None:
            _local_cache[it.series_id] = local
    if num in local:
        return "owned"
    mon = (
        db.query(MonitoredIssue)
        .filter(MonitoredIssue.series_id == it.series_id,
                MonitoredIssue.issue_number == num,
                MonitoredIssue.issue_type == "regular")
        .first()
    )
    return "monitored" if mon else "missing"


@app.get("/api/reading-lists/search")
def api_reading_list_search(
    name: str = "", publisher: str = "", list_type: str = "",
    attribution_source: str = "", average_rating__gte: str = "",
):
    """Search public Metron reading lists (cached). Filters mirror Metron's."""
    from metadata.metron_client import RateLimitedError
    from metadata.metron_reading_lists import search_reading_lists
    key = (name, publisher, list_type, attribution_source, average_rating__gte)
    now = time.time()
    hit = _RL_SEARCH_CACHE.get(key)
    if hit and now - hit[0] < _RL_SEARCH_TTL:
        return {"results": hit[1]}
    try:
        results = search_reading_lists(
            name=name, publisher=publisher, list_type=list_type,
            attribution_source=attribution_source, average_rating__gte=average_rating__gte,
        )
    except RateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    _RL_SEARCH_CACHE[key] = (now, results)
    return {"results": results}


@app.get("/api/reading-lists/metron/{metron_id}/preview")
def api_reading_list_preview(metron_id: int, db: Session = Depends(get_db)):
    """Detail + items annotated with what the app already tracks/owns."""
    from metadata.metron_client import RateLimitedError
    from metadata.metron_reading_lists import get_reading_list_detail, get_reading_list_items, parse_item
    try:
        detail = get_reading_list_detail(metron_id)
        items = [parse_item(r) for r in get_reading_list_items(metron_id)]
    except RateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    local_cache: dict[int, set] = {}
    type_counts: dict[str, int] = {}
    for it in items:
        s = _find_local_series(db, it["series_name"], it["series_year"])
        owned = False
        if s:
            nums = local_cache.setdefault(s.id, _local_issue_numbers(s))
            owned = _norm_issue_num(it["number"]) in nums
        it["series_tracked"] = s is not None
        it["owned"] = owned
        type_counts[it["issue_type"] or ""] = type_counts.get(it["issue_type"] or "", 0) + 1

    return {
        "metron_id": metron_id,
        "name": detail.get("name"),
        "desc": detail.get("desc"),
        "image_url": detail.get("image"),
        "list_type": detail.get("list_type"),
        "attribution_source": detail.get("attribution_source"),
        "attribution_url": detail.get("attribution_url"),
        "average_rating": detail.get("average_rating"),
        "issue_type_counts": type_counts,
        "items": items,
    }


class ReadingListAdd(BaseModel):
    metron_id: int
    issue_types: list[str] | None = None  # None → monitor all; [] → monitor none; [..] → only those


@app.post("/api/reading-lists", status_code=201)
def api_reading_list_add(payload: ReadingListAdd, db: Session = Depends(get_db)):
    """Mirror a Metron reading list locally, create/find its series, and monitor
    only the issues whose issue_type is selected (all if none given)."""
    from metadata.metron_client import RateLimitedError
    from metadata.metron_reading_lists import get_reading_list_detail, get_reading_list_items, parse_item
    try:
        detail = get_reading_list_detail(payload.metron_id)
        parsed = [parse_item(r) for r in get_reading_list_items(payload.metron_id)]
    except RateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    # None → monitor all types; [] → monitor none (an explicit user choice).
    monitor_all = payload.issue_types is None
    selected = set(payload.issue_types or [])
    rl = db.query(ReadingList).filter(ReadingList.metron_id == payload.metron_id).first()
    if not rl:
        rl = ReadingList(metron_id=payload.metron_id, name=detail.get("name") or "Reading List")
        db.add(rl)
    rl.name = detail.get("name") or rl.name
    rl.slug = detail.get("slug")
    rl.list_type = detail.get("list_type")
    rl.attribution_source = detail.get("attribution_source")
    rl.attribution_url = detail.get("attribution_url")
    rl.image_url = detail.get("image")
    rl.desc = detail.get("desc")
    rl.average_rating = detail.get("average_rating")
    rl.num_items = len(parsed)
    rl.monitored_issue_types = _serialize_monitor_types(payload.issue_types)
    rl.synced_at = datetime.now(timezone.utc)
    db.flush()

    # Replace items wholesale (cheap; lists are small).
    db.query(ReadingListItem).filter(ReadingListItem.reading_list_id == rl.id).delete()

    # The Metron items payload has no series id, so link by name + year (matches
    # already-tracked series with no Metron call; resolves/creates the rest).
    series_cache: dict[tuple, Series | None] = {}
    for p in parsed:
        key = ((p["series_name"] or "").strip().lower(), p["series_year"], p.get("series_volume"))
        if key not in series_cache:
            series_cache[key] = _resolve_series_for_reading_list(
                db, p["series_name"], p["series_year"], p.get("series_volume"),
            )
        s = series_cache[key]
        item = ReadingListItem(
            reading_list_id=rl.id,
            order=p["order"],
            issue_type=p["issue_type"],
            metron_issue_id=p["metron_issue_id"],
            metron_series_id=(s.metron_series_id if s else None),
            series_name=p["series_name"],
            series_year=p["series_year"],
            number=p["number"],
            cover_year=p["cover_year"],
            cv_issue_id=p["cv_issue_id"],
            cv_series_id=(s.comicvine_volume_id if s else None),
            series_id=(s.id if s else None),
        )
        db.add(item)
        # Monitor selected issue types (all when issue_types was None).
        if s and (monitor_all or p["issue_type"] in selected):
            num = _norm_issue_num(p["number"])
            exists = (
                db.query(MonitoredIssue)
                .filter(MonitoredIssue.series_id == s.id,
                        MonitoredIssue.issue_number == num,
                        MonitoredIssue.issue_type == "regular")
                .first()
            )
            if not exists:
                db.add(MonitoredIssue(series_id=s.id, issue_number=num, issue_type="regular"))

    db.commit()
    db.refresh(rl)
    return _reading_list_dict(rl, db)


@app.get("/api/reading-lists")
def api_reading_lists(db: Session = Depends(get_db)):
    lists = db.query(ReadingList).order_by(ReadingList.added_at.desc()).all()
    return {"reading_lists": [_reading_list_dict(rl, db) for rl in lists]}


@app.get("/api/reading-lists/{rl_id}")
def api_reading_list_detail(rl_id: int, db: Session = Depends(get_db)):
    rl = db.get(ReadingList, rl_id)
    if not rl:
        raise HTTPException(status_code=404)
    items = (
        db.query(ReadingListItem)
        .filter(ReadingListItem.reading_list_id == rl.id)
        .order_by(ReadingListItem.order)
        .all()
    )
    local_cache: dict[int, set] = {}
    return {
        **_reading_list_dict(rl, db),
        "items": [
            {
                "order": it.order,
                "issue_type": it.issue_type,
                "series_name": it.series_name,
                "number": it.number,
                "cover_year": it.cover_year,
                "series_id": it.series_id,
                "status": _item_status(it, db, local_cache),
            }
            for it in items
        ],
    }


@app.post("/api/reading-lists/{rl_id}/resync")
def api_reading_list_resync(rl_id: int, db: Session = Depends(get_db)):
    rl = db.get(ReadingList, rl_id)
    if not rl:
        raise HTTPException(status_code=404)
    # Preserve the all (None) vs none ([]) vs specific distinction across resync.
    return api_reading_list_add(
        ReadingListAdd(metron_id=rl.metron_id, issue_types=_deserialize_monitor_types(rl.monitored_issue_types)),
        db,
    )


@app.delete("/api/reading-lists/{rl_id}")
def api_reading_list_delete(rl_id: int, db: Session = Depends(get_db)):
    rl = db.get(ReadingList, rl_id)
    if not rl:
        raise HTTPException(status_code=404)
    db.query(ReadingListItem).filter(ReadingListItem.reading_list_id == rl.id).delete()
    db.delete(rl)  # series, files and monitoring are intentionally left untouched
    db.commit()
    return {"ok": True}


@app.get("/api/reading-lists/{rl_id}/cbl")
def api_reading_list_cbl(rl_id: int, db: Session = Depends(get_db)):
    from web.cbl import build_cbl
    rl = db.get(ReadingList, rl_id)
    if not rl:
        raise HTTPException(status_code=404)
    items = (
        db.query(ReadingListItem)
        .filter(ReadingListItem.reading_list_id == rl.id)
        .order_by(ReadingListItem.order)
        .all()
    )
    xml = build_cbl(rl.name, items)
    safe = re.sub(r'[\\/:*?"<>|]', "-", rl.name)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{safe}.cbl"'},
    )


@app.get("/api/komga/status")
def api_komga_status():
    from web import komga_client
    return {"configured": komga_client.is_configured()}


def _push_reading_list_komga(rl: ReadingList, db: Session) -> dict:
    """Create/update the Komga read list for a ReadingList. Shared by the manual
    push route and the nightly auto-push job. Assumes Komga is configured."""
    from web import komga_client
    items = (
        db.query(ReadingListItem)
        .filter(ReadingListItem.reading_list_id == rl.id)
        .order_by(ReadingListItem.order)
        .all()
    )
    entries = [(it.series_name or "", it.number or "") for it in items]
    return komga_client.push_reading_list(rl.name, "Imported from Metron reading list", entries)


@app.post("/api/reading-lists/{rl_id}/push-komga")
def api_reading_list_push_komga(rl_id: int, db: Session = Depends(get_db)):
    from web import komga_client
    if not komga_client.is_configured():
        raise HTTPException(status_code=400, detail="Komga not configured (set KOMGA_URL + KOMGA_API_KEY).")
    rl = db.get(ReadingList, rl_id)
    if not rl:
        raise HTTPException(status_code=404)
    try:
        return _push_reading_list_komga(rl, db)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Komga request failed: {exc}")


# ── Reading-list suggestions (Phase B — bounded scan, manual) ─────────────────
# Namespaced separately from /api/reading-lists/{id} to avoid the int-path clash.

@app.post("/api/reading-list-suggestions/scan")
def api_suggest_scan():
    from web import reading_list_suggest
    started = reading_list_suggest.run_scan()
    return {"started": started, **reading_list_suggest.get_status()}


@app.get("/api/reading-list-suggestions/status")
def api_suggest_status():
    from web import reading_list_suggest
    return reading_list_suggest.get_status()


@app.get("/api/reading-list-suggestions/settings")
def api_suggest_settings_get(db: Session = Depends(get_db)):
    return {
        "threshold": int(_get_setting(db, "rl_suggest_threshold", "50")),
        "min_rating": float(_get_setting(db, "rl_suggest_min_rating", "3")),
        "max_lists": int(_get_setting(db, "rl_suggest_max", "200")),
    }


class SuggestSettings(BaseModel):
    threshold: int | None = None
    min_rating: float | None = None
    max_lists: int | None = None


@app.put("/api/reading-list-suggestions/settings")
def api_suggest_settings_put(payload: SuggestSettings, db: Session = Depends(get_db)):
    if payload.threshold is not None:
        _set_setting(db, "rl_suggest_threshold", str(max(1, min(100, payload.threshold))))
    if payload.min_rating is not None:
        _set_setting(db, "rl_suggest_min_rating", str(payload.min_rating))
    if payload.max_lists is not None:
        _set_setting(db, "rl_suggest_max", str(max(1, payload.max_lists)))
    return api_suggest_settings_get(db)


@app.get("/api/reading-list-suggestions")
def api_suggestions(db: Session = Depends(get_db)):
    from web.models import SuggestedReadingList
    threshold = int(_get_setting(db, "rl_suggest_threshold", "50")) / 100.0
    added = {r.metron_id for r in db.query(ReadingList.metron_id).all()}
    rows = (
        db.query(SuggestedReadingList)
        .filter(SuggestedReadingList.coverage >= threshold)
        .order_by(SuggestedReadingList.coverage.desc())
        .all()
    )
    return {"suggestions": [
        {
            "metron_id": r.metron_id, "name": r.name, "image_url": r.image_url,
            "list_type": r.list_type, "attribution_source": r.attribution_source,
            "average_rating": r.average_rating, "owned": r.owned, "total": r.total,
            "coverage": round(r.coverage * 100),
        }
        for r in rows if r.metron_id not in added
    ]}


# ── React SPA (the UI) ───────────────────────────────────────────────────────
# Served at the root. Hashed assets via StaticFiles at /assets; every other
# non-API path falls back to index.html for client-side routing. Registered
# LAST so the /api/* + /health routes above always match first; the catch-all
# only ever sees paths nothing else claimed.
SPA_DIST = os.path.join(os.path.dirname(__file__), os.pardir, "frontend", "dist")

if os.path.isdir(os.path.join(SPA_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(SPA_DIST, "assets")), name="spa-assets")
    _spa_root = os.path.normpath(SPA_DIST) + os.sep  # trailing sep → real dir boundary, not a prefix match

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str = ""):
        # never hijack the JSON API / health namespaces — an unmatched /api/*
        # must 404 as JSON, not silently return the SPA shell.
        if full_path == "health" or full_path.startswith(("api/", "assets/")):
            raise HTTPException(status_code=404)
        candidate = os.path.normpath(os.path.join(SPA_DIST, full_path))
        # serve root-level static files (vite.svg, favicon); else the SPA shell
        if full_path and candidate.startswith(_spa_root) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(SPA_DIST, "index.html"))
