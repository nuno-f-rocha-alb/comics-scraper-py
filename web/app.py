import json
import logging
import os
import re
from contextlib import asynccontextmanager
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

log = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from web.database import SessionLocal, init_db
from web.models import AppSetting, DownloadJob, MetronCache, MetronIssueCache, MonitoredIssue, Series

COMICS_BASE_DIR = os.getenv("COMICS_BASE_DIR", "/app/comics")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from web import worker
    worker.start()
    yield


app = FastAPI(title="Comics Scraper", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


def _fmt_date(value: str) -> str:
    if not value:
        return ""
    try:
        d = date.fromisoformat(str(value)[:10])
        return d.strftime("%b %d, %Y")
    except ValueError:
        return str(value)


def _is_future(value: str) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) > date.today()
    except ValueError:
        return False


templates.env.filters["fmt_date"] = _fmt_date
templates.env.filters["is_future"] = _is_future


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


def _is_series_ended(s: Series) -> bool:
    """True if Metron's metadata says this series will get no further issues.

    Primary signal is `status` (e.g. Ongoing / Hiatus / Cancelled / Completed
    / Ended), which Metron exposes alongside series_type. status is more
    reliable because series_type can be mislabelled (DC's Absolute Batman is
    tagged "Single Issue" on Metron despite being an ongoing run with 20+
    issues — status correctly says "Ongoing").

    Falls back to year_end + intrinsically single-edition series_type when
    status hasn't been refreshed onto the row yet.
    """
    if s.status:
        return s.status.strip().lower() in _ENDED_STATUSES
    if s.year_end:
        return True
    if s.series_type in _ENDED_SERIES_TYPES:
        return True
    return False


def _monitored_numbers(s: Series, db: Session) -> set[str] | None:
    """Return the set of issue numbers the user is monitoring, or None if the
    user has no explicit selection (i.e., everything in [issue_min, total_issues]
    is implicitly monitored)."""
    rows = (
        db.query(MonitoredIssue)
        .filter(MonitoredIssue.series_id == s.id, MonitoredIssue.issue_type == "regular")
        .all()
    )
    if not rows:
        return None
    return {r.issue_number for r in rows}


def _has_all_monitored_files(s: Series, db: Session) -> bool:
    """True if every issue the user is monitoring has a local file."""
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
    if not _is_series_ended(s):
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
    from metadata.metron_client import get as metron_get
    try:
        r = metron_get(f"{METRON_BASE_URL}/series/{s.metron_series_id}/")
        data = r.json()
    except Exception as exc:
        log.warning("Could not refresh series meta for %s: %s", s.series_name, exc)
        return False

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
    return True


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


def _get_or_fetch_metron_issues(
    metron_series_id: int, db: Session, *,
    force: bool = False, block: bool = True, skip_titles: bool = False,
) -> list[dict]:
    """Return issues from local cache (if fresh) else fetch from Metron and store.

    skip_titles=True skips the per-issue detail call that resolves missing
    titles — use from background jobs that only need the issue list (e.g.,
    refreshing total_issues), because that detail call is the main source of
    burst-rate-limit pressure (N calls per series).
    """
    from datetime import timedelta

    if not force:
        cutoff = datetime.utcnow() - timedelta(days=_ISSUE_CACHE_DAYS)
        first = (
            db.query(MetronIssueCache)
            .filter(MetronIssueCache.series_id == metron_series_id)
            .first()
        )
        if first and first.cached_at > cutoff:
            rows = (
                db.query(MetronIssueCache)
                .filter(MetronIssueCache.series_id == metron_series_id)
                .all()
            )
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
        if not img:
            r2 = metron_get(
                f"{METRON_BASE_URL}/issue/",
                series_id=metron_id,
                ordering="number",
                limit=1,
            )
            issues = r2.json().get("results", [])
            if issues:
                img = _extract_img(issues[0].get("image")) or ""
    except Exception:
        pass

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

@app.get("/api/metron/search", response_class=HTMLResponse)
def metron_search(request: Request, name: str = "", db: Session = Depends(get_db)):
    name = name.strip()
    if len(name) < 2:
        return HTMLResponse("")

    # Search MetronCache locally — zero API calls when cache is populated
    terms = name.split()
    cache_q = db.query(MetronCache)
    for term in terms:
        cache_q = cache_q.filter(MetronCache.name.ilike(f"%{term}%"))
    cache_rows = cache_q.order_by(MetronCache.year_began.desc()).limit(20).all()

    if cache_rows:
        results = [
            {
                "id": r.metron_id,
                "name": r.name,
                "publisher": {"name": r.publisher_name} if r.publisher_name else None,
                "year_began": r.year_began,
                "issue_count": r.issue_count,
                "image": r.image_url or "",
            }
            for r in cache_rows
        ]
        uncached_ids = [r.metron_id for r in cache_rows if r.image_url is None]
        return templates.TemplateResponse(
            "partials/metron_results.html",
            {"request": request, "results": results, "query": name, "uncached_ids": uncached_ids},
        )

    # Fall back to Metron API (cache empty or no match)
    try:
        from config import METRON_BASE_URL
        from metadata.metron_client import get as metron_get
        r = metron_get(f"{METRON_BASE_URL}/series/", name=name)
        results = r.json().get("results", [])
        uncached_ids = []
        if results:
            ids = [s["id"] for s in results if s.get("id")]
            cached_map = {
                c.metron_id: c
                for c in db.query(MetronCache).filter(MetronCache.metron_id.in_(ids)).all()
            }
            for s in results:
                sid = s.get("id")
                c = cached_map.get(sid)
                if c and c.image_url:
                    s["image"] = c.image_url
                elif not _extract_img(s.get("image")):
                    uncached_ids.append(sid)
        return templates.TemplateResponse(
            "partials/metron_results.html",
            {"request": request, "results": results, "query": name, "uncached_ids": uncached_ids},
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger mt-2">Metron search error: {exc}</div>'
        )


@app.get("/api/metron/search-pick", response_class=HTMLResponse)
def metron_search_pick(request: Request, name: str = "", field: str = "", db: Session = Depends(get_db)):
    name = name.strip()
    if len(name) < 2:
        return HTMLResponse("")

    # Search MetronCache locally — zero API calls when cache is populated
    terms = name.split()
    cache_q = db.query(MetronCache)
    for term in terms:
        cache_q = cache_q.filter(MetronCache.name.ilike(f"%{term}%"))
    cache_rows = cache_q.order_by(MetronCache.year_began.desc()).limit(20).all()

    if cache_rows:
        results = [
            {
                "id": r.metron_id,
                "name": r.name,
                "publisher": {"name": r.publisher_name} if r.publisher_name else None,
                "year_began": r.year_began,
                "series_type": {"name": r.series_type} if r.series_type else None,
            }
            for r in cache_rows
        ]
        return templates.TemplateResponse(
            "partials/metron_pick_results.html",
            {"request": request, "results": results, "field": field},
        )

    # Fall back to Metron API (cache empty or no match)
    try:
        from config import METRON_BASE_URL
        from metadata.metron_client import get as metron_get
        r = metron_get(f"{METRON_BASE_URL}/series/", name=name)
        results = r.json().get("results", [])
        return templates.TemplateResponse(
            "partials/metron_pick_results.html",
            {"request": request, "results": results, "field": field},
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger mt-2 small">Search error: {exc}</div>'
        )


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


@app.get("/api/metron/series/{metron_id}/cover", response_class=HTMLResponse)
def metron_series_cover(metron_id: int, db: Session = Depends(get_db)):
    img = _ensure_cover_cached(metron_id, db)
    if img:
        return HTMLResponse(f'<img src="{img}" class="cover-img" alt="">')
    return HTMLResponse('<div class="cover-placeholder"><i class="bi bi-book"></i></div>')


@app.get("/api/metron/batch-covers", response_class=HTMLResponse)
def metron_batch_covers(ids: str = "", db: Session = Depends(get_db)):
    """Fetch/cache covers for multiple series; returns HTMX OOB swap fragments."""
    if not ids:
        return HTMLResponse("")
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    parts: list[str] = []
    for mid in id_list:
        img = _ensure_cover_cached(mid, db)
        if img:
            parts.append(
                f'<img id="cover-{mid}" src="{img}" class="cover-img" alt="" hx-swap-oob="true">'
            )
        else:
            parts.append(
                f'<div id="cover-{mid}" class="cover-placeholder" hx-swap-oob="true">'
                f'<i class="bi bi-book"></i></div>'
            )
    return HTMLResponse("\n".join(parts))


@app.post("/api/metron/cache/refresh")
def metron_cache_refresh(db: Session = Depends(get_db)):
    """Paginate all Metron series and upsert metadata into local cache."""
    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get

    url = f"{METRON_BASE_URL}/series/"
    added = updated = 0

    while url:
        try:
            r = metron_get(url)
            data = r.json()
        except Exception as exc:
            msg = f"Cache refresh stopped ({added} new, {updated} updated): {exc}"
            return RedirectResponse(url=f"/series?msg={quote(msg)}", status_code=303)

        now = datetime.now(timezone.utc)
        for s in data.get("results", []):
            mid = s.get("id")
            if not mid:
                continue
            pub = s.get("publisher") or {}
            st = s.get("series_type") or {}
            existing = db.get(MetronCache, mid)
            if existing is None:
                db.add(MetronCache(
                    metron_id=mid,
                    name=s.get("name") or s.get("series"),
                    publisher_name=pub.get("name") if isinstance(pub, dict) else None,
                    year_began=s.get("year_began"),
                    issue_count=s.get("issue_count"),
                    series_type=st.get("name") if isinstance(st, dict) else None,
                    cv_id=None,
                    image_url=None,  # populated lazily on first cover request
                    cached_at=now,
                ))
                added += 1
            else:
                if s.get("name") or s.get("series"):
                    existing.name = s.get("name") or s.get("series")
                pub_name = pub.get("name") if isinstance(pub, dict) else None
                if pub_name:
                    existing.publisher_name = pub_name
                if s.get("year_began"):
                    existing.year_began = s["year_began"]
                if s.get("issue_count"):
                    existing.issue_count = s["issue_count"]
                st_name = st.get("name") if isinstance(st, dict) else None
                if st_name:
                    existing.series_type = st_name
                existing.cached_at = now
                updated += 1

        db.commit()
        url = data.get("next")

    # Sync covers + issue lists for tracked series
    tracked = db.query(Series).filter(Series.metron_series_id.isnot(None)).all()
    covers_synced = issues_synced = 0
    for s in tracked:
        mid = s.metron_series_id
        # Populate cover for MetronCache entry if not yet tried
        cached = db.get(MetronCache, mid)
        if cached is None or cached.image_url is None:
            _ensure_cover_cached(mid, db)
            covers_synced += 1
        # Pre-populate issue list if no cache exists
        existing = db.query(MetronIssueCache).filter(MetronIssueCache.series_id == mid).first()
        if not existing:
            try:
                _get_or_fetch_metron_issues(mid, db)
                issues_synced += 1
            except Exception:
                pass
        # Annual series
        if s.metron_annual_series_id:
            ann_existing = (
                db.query(MetronIssueCache)
                .filter(MetronIssueCache.series_id == s.metron_annual_series_id)
                .first()
            )
            if not ann_existing:
                try:
                    _get_or_fetch_metron_issues(s.metron_annual_series_id, db)
                    issues_synced += 1
                except Exception:
                    pass

    parts = [f"Metron cache refreshed: {added} new, {updated} updated"]
    if covers_synced:
        parts.append(f"{covers_synced} covers synced")
    if issues_synced:
        parts.append(f"{issues_synced} issue lists cached")
    msg = ". ".join(parts) + "."
    return RedirectResponse(url=f"/series?msg={quote(msg)}", status_code=303)


@app.get("/api/metron/series/{metron_id}/add-form", response_class=HTMLResponse)
def metron_series_add_form(request: Request, metron_id: int, db: Session = Depends(get_db)):
    cached = db.get(MetronCache, metron_id)
    if cached is not None:
        data = {
            "id": cached.metron_id,
            "name": cached.name,
            "image": cached.image_url or "",
            "publisher": {"name": cached.publisher_name} if cached.publisher_name else None,
            "year_began": cached.year_began,
            "issue_count": cached.issue_count,
            "cv_id": cached.cv_id,
        }
        return templates.TemplateResponse("partials/add_form.html", {"request": request, "series": data})
    try:
        from config import METRON_BASE_URL
        from metadata.metron_client import get as metron_get
        r = metron_get(f"{METRON_BASE_URL}/series/{metron_id}/")
        data = r.json()
        return templates.TemplateResponse(
            "partials/add_form.html",
            {"request": request, "series": data},
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger mt-2">Error loading series: {exc}</div>'
        )


# ── Cover + ID sync ────────────────────────────────────────────────────────────

@app.post("/api/sync-covers")
def sync_covers(db: Session = Depends(get_db)):
    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get

    rows = db.query(Series).all()
    found_ids = 0
    updated_covers = 0

    for s in rows:
        # Phase 1: auto-find metron_series_id from comicvine_volume_id
        if not s.metron_series_id and s.comicvine_volume_id:
            try:
                r = metron_get(f"{METRON_BASE_URL}/series/", cv_id=s.comicvine_volume_id)
                results = r.json().get("results", [])
                if results:
                    s.metron_series_id = results[0]["id"]
                    found_ids += 1
            except Exception:
                pass

        # Phase 2: refresh cover + issue count + ended status from Metron detail.
        # Runs whenever the series has a metron_series_id — the button is a
        # manual "Refresh from Metron" so it must always re-sync.
        if s.metron_series_id:
            if _refresh_series_meta_from_metron(s, db):
                # Fallback: use first issue cover if series still has no image
                if not s.cover_image_url:
                    try:
                        r2 = metron_get(
                            f"{METRON_BASE_URL}/issue/",
                            series_id=s.metron_series_id,
                            ordering="number",
                            limit=1,
                        )
                        issues = r2.json().get("results", [])
                        if issues:
                            img2 = issues[0].get("image") or ""
                            s.cover_image_url = img2 if isinstance(img2, str) and img2 else None
                    except Exception:
                        pass
                _recompute_pause_state(s, db)
                updated_covers += 1

    db.commit()
    parts = []
    if found_ids:
        parts.append(f"{found_ids} Metron IDs found via CV ID")
    if updated_covers:
        parts.append(f"{updated_covers} covers synced")
    msg = ", ".join(parts) if parts else "Nothing to update."
    return RedirectResponse(url=f"/series?msg={quote(msg)}", status_code=303)


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


@app.get("/api/verify-search", response_class=HTMLResponse)
def verify_search(
    request: Request,
    getcomics_search_name: str = "",
    series_name: str = "",
):
    search_term = getcomics_search_name.strip() or series_name.strip()
    if not search_term:
        return HTMLResponse("")
    try:
        comics = _getcomics_verify(search_term)
        return templates.TemplateResponse(
            "partials/verify_results.html",
            {"request": request, "comics": comics, "search_term": search_term},
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger mt-2 py-2 small">Verify error: {exc}</div>'
        )


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

@app.post("/series/{series_id}/monitor-all")
def monitor_all(series_id: int, db: Session = Depends(get_db)):
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
    return Response(status_code=200, headers={"HX-Trigger": "refresh-issues"})


@app.delete("/series/{series_id}/monitor-all")
def unmonitor_all(series_id: int, db: Session = Depends(get_db)):
    db.query(MonitoredIssue).filter(MonitoredIssue.series_id == series_id).delete()
    s = db.query(Series).filter(Series.id == series_id).first()
    if s:
        _recompute_pause_state(s, db)
    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": "refresh-issues"})


@app.post("/series/{series_id}/issues/{number}/monitor", response_class=HTMLResponse)
def issue_monitor_toggle(
    series_id: int,
    number: str,
    type: str = Query(default="regular"),
    db: Session = Depends(get_db),
):
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

    return HTMLResponse(_monitor_btn(series_id, number, monitored, issue_type))


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


@app.post("/series/{series_id}/issues/{number}/download", response_class=HTMLResponse)
def issue_download(series_id: int, number: str, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    # Don't queue a duplicate for the same issue already in progress
    existing = (
        db.query(DownloadJob)
        .filter(
            DownloadJob.series_id == series_id,
            DownloadJob.issue_number == number,
            DownloadJob.status.in_(["queued", "downloading"]),
        )
        .first()
    )
    if not existing:
        search_name = s.getcomics_search_name or s.series_name
        job = DownloadJob(
            series_id=series_id,
            issue_number=number,
            search_term=f"{search_name} #{number} ({s.year})",
            status="queued",
        )
        db.add(job)
        db.commit()
        from web import worker
        worker.enqueue(job.id)

    return HTMLResponse(
        '<span class="badge" style="background:rgba(255,193,7,0.15);color:#ffc107;">'
        '<i class="bi bi-hourglass-split me-1"></i>Queued</span>'
    )


@app.get("/downloads", response_class=HTMLResponse)
def downloads_page(request: Request, db: Session = Depends(get_db)):
    jobs = (
        db.query(DownloadJob)
        .order_by(DownloadJob.created_at.desc())
        .limit(200)
        .all()
    )
    series_map = {s.id: s for s in db.query(Series).all()}
    return templates.TemplateResponse(
        "downloads.html",
        {"request": request, "jobs": jobs, "series_map": series_map},
    )


@app.get("/downloads/active", response_class=HTMLResponse)
def downloads_active(request: Request, db: Session = Depends(get_db)):
    from web.worker import get_progress
    active = (
        db.query(DownloadJob)
        .filter(DownloadJob.status.in_(["queued", "downloading"]))
        .order_by(DownloadJob.created_at)
        .all()
    )
    series_map = {s.id: s for s in db.query(Series).all()}
    progress_map = {j.id: get_progress(j.id) for j in active}
    return templates.TemplateResponse(
        "partials/downloads_active.html",
        {
            "request": request,
            "jobs": active,
            "series_map": series_map,
            "progress_map": progress_map,
        },
    )


@app.delete("/downloads/{job_id}")
def download_delete(job_id: int, db: Session = Depends(get_db)):
    job = db.get(DownloadJob, job_id)
    if job and job.status not in ("queued", "downloading"):
        db.delete(job)
        db.commit()
    return Response(status_code=200)


@app.post("/downloads/{job_id}/cancel")
def download_cancel(job_id: int, db: Session = Depends(get_db)):
    """Cancel a queued or in-flight job.

    Queued: flip status to 'cancelled' directly; worker will skip it on pickup.
    Downloading: request cancellation — the worker checks between HTTP calls
    and inside the chunk loop and will abort within ~0.5MB.
    """
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
    # done / failed / cancelled — nothing to do
    return Response(status_code=200, headers={"HX-Trigger": "refresh-downloads"})


@app.delete("/downloads", response_class=HTMLResponse)
def downloads_clear(db: Session = Depends(get_db)):
    db.query(DownloadJob).filter(
        DownloadJob.status.in_(["done", "failed", "cancelled"])
    ).delete()
    db.commit()
    return HTMLResponse(
        '<tr><td colspan="7" class="text-center text-muted py-5">'
        '<i class="bi bi-inbox fs-2 d-block mb-2 opacity-50"></i>'
        'No downloads yet. Click Download on a missing issue to start.'
        "</td></tr>"
    )


@app.get("/downloads/badge", response_class=HTMLResponse)
def downloads_badge(db: Session = Depends(get_db)):
    count = (
        db.query(DownloadJob)
        .filter(DownloadJob.status.in_(["queued", "downloading"]))
        .count()
    )
    if count:
        return HTMLResponse(
            f'<span id="dl-badge" class="badge rounded-pill ms-auto"'
            f' style="background:#0d6efd;font-size:0.65rem;">{count}</span>'
        )
    return HTMLResponse('<span id="dl-badge"></span>')


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

@app.get("/scheduler", response_class=HTMLResponse)
def scheduler_page(request: Request):
    from web.scheduler import get_status
    return templates.TemplateResponse(
        "scheduler.html", {"request": request, "status": get_status()}
    )


@app.get("/scheduler/status", response_class=HTMLResponse)
def scheduler_status(request: Request):
    from web.scheduler import get_status
    return templates.TemplateResponse(
        "partials/scheduler_status.html", {"request": request, "status": get_status()}
    )


@app.post("/scheduler/run")
def scheduler_run():
    from web.scheduler import is_running, trigger_now
    if not is_running():
        trigger_now()
    return RedirectResponse(url="/scheduler", status_code=303)


@app.post("/scheduler/config")
def scheduler_config(
    mode: str = Form(...),
    value: str = Form(...),
):
    from web.scheduler import update_schedule
    try:
        update_schedule(mode.strip(), value.strip())
        msg = f"Schedule updated: {'every ' + value + 'h' if mode == 'interval' else value}"
        return RedirectResponse(url=f"/scheduler?msg={quote(msg)}", status_code=303)
    except Exception as exc:
        return RedirectResponse(
            url=f"/scheduler?error={quote(str(exc))}", status_code=303
        )


# ── Scheduler JSON API (React) ──────────────────────────────────────────────────


def _scheduler_status_json() -> dict:
    from web.scheduler import get_status
    st = get_status()
    return {
        "running": st["running"],
        "last_run_at": st["last_run_at"].isoformat() if st["last_run_at"] else None,
        "last_run_error": st["last_run_error"],
        "next_run_at": st["next_run_at"].isoformat() if st["next_run_at"] else None,
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

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/series")


def _series_overview(db: Session):
    """Shared computation for the series grid: rows + per-series local counts,
    status classification, and footer stats. Used by both the Jinja page and
    the JSON API so the two never drift."""
    rows = db.query(Series).order_by(Series.publisher, Series.series_name).all()
    local_counts = {s.id: _count_local_issues(s) for s in rows}

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

    def has_missing_past(s: Series) -> bool:
        """True if any issue with store_date <= today (or unknown) is missing
        from the local folder. Future-only gaps don't count."""
        local_reg = _local_issue_numbers(s)
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
                # Missing — was it supposed to be out by now?
                # No store_date → assume past (Metron often backfills dates).
                if not c.store_date or c.store_date <= today_iso:
                    return True
        return False

    # Per-series classification used for card border colour + footer stats.
    statuses: dict[int, str] = {}
    for s in rows:
        ended = _is_series_ended(s)
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
        "ended": sum(1 for s in rows if _is_series_ended(s)),
        "continuing": sum(1 for s in rows if not _is_series_ended(s)),
        "monitored": sum(1 for s in rows if s.enabled),
        "unmonitored": sum(1 for s in rows if not s.enabled),
        "issues_total": sum((s.total_issues or 0) for s in rows),
        "files_total": sum(local_counts.values()),
    }

    return rows, local_counts, statuses, stats


@app.get("/series", response_class=HTMLResponse)
def series_list(request: Request, db: Session = Depends(get_db)):
    rows, local_counts, statuses, stats = _series_overview(db)
    return templates.TemplateResponse(
        "series_list.html",
        {
            "request": request,
            "series": rows,
            "local_counts": local_counts,
            "statuses": statuses,
            "stats": stats,
        },
    )


@app.get("/api/series/overview")
def api_series_overview(db: Session = Depends(get_db)):
    """JSON backing the React series grid — same data as the Jinja page."""
    rows, local_counts, statuses, stats = _series_overview(db)
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
            }
            for s in rows
        ],
        "stats": stats,
    }


def _series_dict(s: Series) -> dict:
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
    return _series_dict(s)


@app.get("/api/series/{series_id}")
def api_get_series(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    return _series_dict(s)


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
    return _series_dict(s)


@app.get("/series/add", response_class=HTMLResponse)
def series_add_page(request: Request):
    return templates.TemplateResponse("series_add.html", {"request": request})


@app.post("/series", response_class=HTMLResponse)
def series_create(
    db: Session = Depends(get_db),
    publisher: str = Form(...),
    series_name: str = Form(...),
    year: str = Form(""),
    comicvine_volume_id: str = Form(""),
    metron_series_id: str = Form(""),
    metron_annual_series_id: str = Form(""),
    annual_comicvine_volume_id: str = Form(""),
    getcomics_search_name: str = Form(""),
    cover_image_url: str = Form(""),
    total_issues: str = Form(""),
    issue_min: str = Form("1"),
):
    s = Series(
        publisher=publisher.strip(),
        series_name=series_name.strip(),
        year=int(year) if year.strip() else None,
        comicvine_volume_id=int(comicvine_volume_id) if comicvine_volume_id.strip() else None,
        metron_series_id=int(metron_series_id) if metron_series_id.strip() else None,
        metron_annual_series_id=int(metron_annual_series_id) if metron_annual_series_id.strip() else None,
        annual_comicvine_volume_id=int(annual_comicvine_volume_id) if annual_comicvine_volume_id.strip() else None,
        getcomics_search_name=getcomics_search_name.strip() or None,
        cover_image_url=cover_image_url.strip() or None,
        total_issues=int(total_issues) if total_issues.strip() else None,
        issue_min=int(issue_min) if issue_min.strip() else 1,
    )
    db.add(s)
    db.commit()
    return RedirectResponse(
        url=f"/series?msg={quote(s.series_name + ' added successfully.')}",
        status_code=303,
    )


@app.get("/series/{series_id}/edit", response_class=HTMLResponse)
def series_edit_page(request: Request, series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("series_edit.html", {"request": request, "s": s})


@app.post("/series/{series_id}/update", response_class=HTMLResponse)
def series_update(
    series_id: int,
    db: Session = Depends(get_db),
    publisher: str = Form(...),
    series_name: str = Form(...),
    year: str = Form(""),
    comicvine_volume_id: str = Form(""),
    metron_series_id: str = Form(""),
    metron_annual_series_id: str = Form(""),
    annual_comicvine_volume_id: str = Form(""),
    getcomics_search_name: str = Form(""),
    issue_min: str = Form("1"),
):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    s.publisher = publisher.strip()
    s.series_name = series_name.strip()
    s.year = int(year) if year.strip() else None
    s.comicvine_volume_id = int(comicvine_volume_id) if comicvine_volume_id.strip() else None
    s.metron_series_id = int(metron_series_id) if metron_series_id.strip() else None
    s.metron_annual_series_id = int(metron_annual_series_id) if metron_annual_series_id.strip() else None
    s.annual_comicvine_volume_id = int(annual_comicvine_volume_id) if annual_comicvine_volume_id.strip() else None
    s.getcomics_search_name = getcomics_search_name.strip() or None
    s.issue_min = int(issue_min) if issue_min.strip() else 1
    db.commit()
    return RedirectResponse(
        url=f"/series/{series_id}",
        status_code=303,
    )


@app.patch("/series/{series_id}/toggle", response_class=HTMLResponse)
def series_toggle(request: Request, series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    s.enabled = not s.enabled
    db.commit()
    return templates.TemplateResponse(
        "partials/series_row.html", {"request": request, "s": s}
    )


@app.post("/series/{series_id}/toggle")
def series_toggle_post(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    s.enabled = not s.enabled
    db.commit()
    return RedirectResponse(url=f"/series/{series_id}", status_code=303)


@app.delete("/series/{series_id}")
def series_delete(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if s:
        db.delete(s)
        db.commit()
    return Response(status_code=200)


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


@app.get("/series/{series_id}", response_class=HTMLResponse)
def series_detail(request: Request, series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    local_count = _count_local_issues(s)
    return templates.TemplateResponse(
        "series_detail.html",
        {"request": request, "s": s, "local_count": local_count},
    )


@app.get("/series/{series_id}/issues", response_class=HTMLResponse)
def series_issues_partial(
    request: Request,
    series_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    if not s.metron_series_id:
        return HTMLResponse(
            '<div class="alert alert-info m-3">No Metron ID set — issue list unavailable.</div>'
        )

    local_nums = _local_issue_numbers(s)
    local_annual_nums = _local_annual_issue_numbers(s) if s.metron_annual_series_id else set()

    regular_issues: list[dict] = []
    annual_issues: list[dict] = []

    from metadata.metron_client import RateLimitedError

    try:
        raw = _get_or_fetch_metron_issues(s.metron_series_id, db, force=force, block=False)
        regular_issues = _build_issue_list(raw, local_nums)
    except RateLimitedError as exc:
        secs = int(exc.seconds) + 2
        return HTMLResponse(
            f'<div class="alert alert-warning m-3 d-flex align-items-center gap-2 small">'
            f'  <i class="bi bi-hourglass-split fs-5"></i>'
            f'  <span>Limite de pedidos ao Metron atingido — a retomar em <strong>{secs}s</strong></span>'
            f'</div>'
            f'<div hx-get="/series/{series_id}/issues" hx-trigger="load delay:{secs}s"'
            f'     hx-target="#issues-container" hx-swap="innerHTML"></div>'
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger m-3">Erro ao obter issues do Metron: {exc}</div>'
        )

    if s.metron_annual_series_id:
        try:
            raw_annual = _get_or_fetch_metron_issues(s.metron_annual_series_id, db, force=force, block=False)
            annual_issues = _build_issue_list(raw_annual, local_annual_nums)
        except (RateLimitedError, Exception):
            pass

    first = (
        db.query(MetronIssueCache)
        .filter(MetronIssueCache.series_id == s.metron_series_id)
        .first()
    )
    monitored_rows = (
        db.query(MonitoredIssue).filter(MonitoredIssue.series_id == s.id).all()
    )
    monitored_regular = {m.issue_number for m in monitored_rows if m.issue_type == "regular"}
    monitored_annual = {m.issue_number for m in monitored_rows if m.issue_type == "annual"}
    has_monitoring = bool(monitored_rows)
    return templates.TemplateResponse(
        "partials/series_issues.html",
        {
            "request": request,
            "s": s,
            "regular_issues": regular_issues,
            "annual_issues": annual_issues,
            "cached_at": first.cached_at if first else None,
            "monitored_regular": monitored_regular,
            "monitored_annual": monitored_annual,
            "has_monitoring": has_monitoring,
        },
    )


# ── Series detail JSON API (React) ──────────────────────────────────────────────


@app.get("/api/series/{series_id}/detail")
def api_series_detail(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    return {**_series_dict(s), "local_count": _count_local_issues(s)}


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
        raw = _get_or_fetch_metron_issues(s.metron_series_id, db, force=force, block=False)
        regular = _build_issue_list(raw, local_nums)
    except RateLimitedError as exc:
        return {"has_metron": True, "rate_limited": int(exc.seconds) + 2, "regular": [], "annual": []}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Metron error: {exc}")

    annual: list[dict] = []
    if s.metron_annual_series_id:
        try:
            annual = _build_issue_list(
                _get_or_fetch_metron_issues(s.metron_annual_series_id, db, force=force, block=False),
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
    monitor_all(series_id, db)  # reuse existing insert logic (returns HX response, ignored)
    return {"ok": True}


@app.delete("/api/series/{series_id}/monitor-all")
def api_unmonitor_all(series_id: int, db: Session = Depends(get_db)):
    unmonitor_all(series_id, db)
    return {"ok": True}


@app.post("/api/series/{series_id}/issues/{number}/download")
def api_issue_download(series_id: int, number: str, db: Session = Depends(get_db)):
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
    if not existing:
        search_name = s.getcomics_search_name or s.series_name
        job = DownloadJob(
            series_id=series_id,
            issue_number=number,
            search_term=f"{search_name} #{number} ({s.year})",
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


@app.get("/library", response_class=HTMLResponse)
def library_page(request: Request):
    return templates.TemplateResponse(
        "library.html", {"request": request, "status": _scanner.get_status()}
    )


@app.get("/library/status", response_class=HTMLResponse)
def library_status(request: Request):
    return templates.TemplateResponse(
        "partials/library_status.html", {"request": request, "status": _scanner.get_status()}
    )


@app.post("/library/scan")
def library_scan(force: bool = Form(default=False), db: Session = Depends(get_db)):
    from retag_comics import load_series_from_db
    series_list = load_series_from_db()
    _scanner.run_scan(series_list, force=force)
    return Response(status_code=200, headers={"HX-Trigger": "refresh-scan-status"})


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


@app.post("/series/{series_id}/scan", response_class=HTMLResponse)
def series_scan(series_id: int, force: bool = Form(default=False), db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    _scanner.run_scan([s.to_scraper_tuple()], force=force)
    return HTMLResponse(
        '<button class="btn btn-sm btn-outline-secondary" disabled>'
        '<i class="bi bi-arrow-repeat me-1"></i>Scan started…'
        '</button>'
    )


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


@app.delete("/series/{series_id}/issues/{issue_num}", response_class=HTMLResponse)
def issue_delete_file(
    request: Request,
    series_id: int,
    issue_num: str,
    type: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    ok, msg = _delete_issue_file(s, issue_num, type)
    return templates.TemplateResponse(
        "partials/toast.html",
        {"request": request, "kind": "success" if ok else "error", "message": msg},
        headers={"HX-Trigger": "refresh-issues"} if ok else None,
    )


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


@app.get("/series/{series_id}/issues/{issue_num}/metadata", response_class=HTMLResponse)
def issue_metadata_form(request: Request, series_id: int, issue_num: str, db: Session = Depends(get_db)):
    from metadata.comicinfo_io import read_comicinfo, empty_fields

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    cbz = _find_issue_file(s, issue_num)
    if not cbz:
        return HTMLResponse(
            '<div class="alert alert-warning m-3">Local file not found for this issue.</div>'
        )

    try:
        fields = read_comicinfo(cbz)
    except Exception as exc:
        log.error("Failed to read ComicInfo.xml from %s: %s", cbz, exc)
        fields = empty_fields()

    return templates.TemplateResponse(
        "partials/issue_metadata_form.html",
        {
            "request": request,
            "s": s,
            "issue_num": issue_num,
            "fields": fields,
            "filename": os.path.basename(cbz),
            "from_metron": False,
        },
    )


@app.get("/series/{series_id}/issues/{issue_num}/metadata/from-metron", response_class=HTMLResponse)
def issue_metadata_from_metron(request: Request, series_id: int, issue_num: str, db: Session = Depends(get_db)):
    from metadata.comicinfo_io import empty_fields
    from metadata.get_comic_metadata import get_comic_metadata

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    cbz = _find_issue_file(s, issue_num)
    fields = empty_fields()

    try:
        entry = s.to_scraper_tuple()
        meta = get_comic_metadata(entry, issue_num)
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

    return templates.TemplateResponse(
        "partials/issue_metadata_form.html",
        {
            "request": request,
            "s": s,
            "issue_num": issue_num,
            "fields": fields,
            "filename": os.path.basename(cbz) if cbz else "",
            "from_metron": True,
        },
    )


@app.post("/series/{series_id}/issues/{issue_num}/metadata", response_class=HTMLResponse)
async def issue_metadata_save(request: Request, series_id: int, issue_num: str, db: Session = Depends(get_db)):
    from metadata.comicinfo_io import write_comicinfo, EDITOR_FIELDS

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    cbz = _find_issue_file(s, issue_num)
    if not cbz:
        return templates.TemplateResponse(
            "partials/toast.html",
            {"request": request, "kind": "error", "message": "Local file not found."},
        )

    form = await request.form()
    fields = {k: (form.get(k) or "").strip() for k in EDITOR_FIELDS}

    try:
        ok = write_comicinfo(cbz, fields)
    except Exception as exc:
        log.error("Failed to write ComicInfo.xml to %s: %s", cbz, exc)
        return templates.TemplateResponse(
            "partials/toast.html",
            {"request": request, "kind": "error", "message": f"Save failed: {exc}"},
        )

    return templates.TemplateResponse(
        "partials/toast.html",
        {
            "request": request,
            "kind": "success" if ok else "error",
            "message": "Metadata saved." if ok else "Save returned failure.",
        },
        headers={"HX-Trigger": "refresh-issues"} if ok else None,
    )


@app.get("/series/{series_id}/series-xml", response_class=HTMLResponse)
def series_xml_form(request: Request, series_id: int, db: Session = Depends(get_db)):
    from metadata.series_xml import read_series_xml

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    fields = read_series_xml(_series_dir(s))
    return templates.TemplateResponse(
        "partials/series_xml_form.html",
        {"request": request, "s": s, "fields": fields},
    )


@app.post("/series/{series_id}/series-xml", response_class=HTMLResponse)
async def series_xml_save(request: Request, series_id: int, db: Session = Depends(get_db)):
    from metadata.series_xml import FIELDS, write_series_xml

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    form = await request.form()
    fields = {k: (form.get(k) or "").strip() for k in FIELDS}

    try:
        write_series_xml(_series_dir(s), fields)
    except Exception as exc:
        log.error("Failed to write series.xml: %s", exc)
        return templates.TemplateResponse(
            "partials/toast.html",
            {"request": request, "kind": "error", "message": f"Save failed: {exc}"},
        )

    return templates.TemplateResponse(
        "partials/toast.html",
        {"request": request, "kind": "success", "message": "Series notes saved."},
    )


@app.get("/series/{series_id}/rename-preview", response_class=HTMLResponse)
def rename_preview(request: Request, series_id: int, msg: str = "", db: Session = Depends(get_db)):
    from retag_comics import _issue_number as _parse_num, expected_filename

    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    entry = s.to_scraper_tuple()

    def _scan(directory: str, scan_entry: tuple) -> list[dict]:
        if not os.path.isdir(directory):
            return []
        results = []
        for filename in sorted(os.listdir(directory)):
            if not filename.lower().endswith((".cbz", ".cbr")):
                continue
            ext = os.path.splitext(filename)[1].lower()
            num = _parse_num(filename)
            exp = expected_filename(scan_entry, num, ext) if num else None
            results.append({
                "folder": directory,
                "current": filename,
                "expected": exp,
                "changed": exp is not None and filename != exp,
            })
        return results

    series_dir = _series_dir(s)
    items = _scan(series_dir, entry)

    annual_entry = (entry[0], f"{entry[1]} Annual", entry[2])
    items += _scan(os.path.join(series_dir, "Annuals"), annual_entry)

    changed = [i for i in items if i["changed"]]
    correct_count = sum(1 for i in items if i["expected"] and not i["changed"])
    unparseable = [i for i in items if i["expected"] is None]

    return templates.TemplateResponse("partials/rename_preview.html", {
        "request": request,
        "s": s,
        "changed": changed,
        "correct_count": correct_count,
        "unparseable": unparseable,
        "msg": msg,
    })


@app.post("/series/{series_id}/rename-apply", response_class=HTMLResponse)
async def rename_apply(request: Request, series_id: int, db: Session = Depends(get_db)):
    form = await request.form()
    renamed = errors = 0
    for value in form.getlist("rename"):
        try:
            folder, current, expected = value.split("|", 2)
            src = os.path.join(folder, current)
            dst = os.path.join(folder, expected)
            base = os.path.realpath(COMICS_BASE_DIR)
            if (os.path.commonpath([base, os.path.realpath(src)]) != base
                    or os.path.commonpath([base, os.path.realpath(dst)]) != base):
                log.warning("Rename: path escapes comics dir: %s -> %s", src, dst)
                errors += 1
            elif not os.path.exists(src):
                log.warning("Rename: source not found: %s", src)
                errors += 1
            elif os.path.exists(dst):
                log.warning("Rename: target already exists: %s", dst)
                errors += 1
            else:
                os.rename(src, dst)
                log.info("Renamed: %s -> %s", current, expected)
                renamed += 1
        except Exception as exc:
            log.error("Rename error: %s", exc)
            errors += 1

    msg = f"{renamed} ficheiro(s) renomeado(s)"
    if errors:
        msg += f", {errors} erro(s)"
    return rename_preview(request, series_id, msg=msg, db=db)


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


def _get_log_setting(db, key: str, default: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else default


def _set_log_setting(db, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


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


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    view: str = "month",
    date_str: str = Query("", alias="date"),
    db: Session = Depends(get_db),
):
    if view not in ("month", "week"):
        view = "month"
    try:
        ref = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        ref = date.today()

    start, end = _calendar_range(view, ref)
    events = _load_calendar_events(db, start, end)

    # Build the list of weeks (each = 7 dicts with date + events).
    weeks: list[list[dict]] = []
    cursor = start
    today = date.today()
    while cursor <= end:
        week: list[dict] = []
        for _ in range(7):
            week.append({
                "date": cursor,
                "iso": cursor.isoformat(),
                "is_today": cursor == today,
                "in_view_month": (view == "week") or cursor.month == ref.month,
                "events": events.get(cursor.isoformat(), []),
            })
            cursor += timedelta(days=1)
        weeks.append(week)

    prev_ref = _calendar_shift(view, ref, -1).isoformat()
    next_ref = _calendar_shift(view, ref, +1).isoformat()
    today_iso = date.today().isoformat()

    if view == "week":
        header_label = f"{start.strftime('%b %d')} — {end.strftime('%b %d, %Y')}"
    else:
        header_label = ref.strftime("%B %Y")

    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request,
            "view": view,
            "weeks": weeks,
            "header_label": header_label,
            "prev_ref": prev_ref,
            "next_ref": next_ref,
            "today_iso": today_iso,
            "current_ref": ref.isoformat(),
        },
    )


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
    from util import normalize_title

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
        try:
            n = str(int(float(j.issue_number)))
        except (ValueError, TypeError):
            n = j.issue_number or ""
        in_flight.add((j.series_id, n))

    results: list[dict] = []
    for e in entries:
        if not e.series_name:
            continue
        norm = normalize_title(e.series_name)
        s = by_norm.get(norm)
        if not s:
            continue
        try:
            num_norm = str(int(float(e.issue_number)))
        except (ValueError, TypeError):
            num_norm = e.issue_number or ""

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


@app.get("/releases", response_class=HTMLResponse)
def releases_page(request: Request, db: Session = Depends(get_db)):
    from comic_search.rss_feed import fetch_feed
    error = None
    entries = []
    matches: list[dict] = []
    try:
        entries = fetch_feed()
        matches = _match_feed_entries(entries, db)
    except Exception as exc:
        log.warning("Failed to fetch RSS feed: %s", exc)
        error = str(exc)

    return templates.TemplateResponse(
        "releases.html",
        {
            "request": request,
            "matches": matches,
            "feed_size": len(entries),
            "error": error,
        },
    )


@app.get("/releases/list", response_class=HTMLResponse)
def releases_list_partial(request: Request, db: Session = Depends(get_db)):
    """HTMX partial — re-renders just the matches list. Refresh button target."""
    from comic_search.rss_feed import fetch_feed
    try:
        entries = fetch_feed()
        matches = _match_feed_entries(entries, db)
        return templates.TemplateResponse(
            "partials/releases_list.html",
            {"request": request, "matches": matches, "feed_size": len(entries)},
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger small">Erro ao obter feed: {exc}</div>'
        )


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


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    files = _log_files()
    retention = int(_get_log_setting(db, "log_retention_days", "7"))
    current = _current_log_path()
    current_name = os.path.basename(current) if current else (files[0]["name"] if files else "")
    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "files": files,
            "retention_days": retention,
            "current_name": current_name,
            "lines_default": _LOG_LINES_DEFAULT,
        },
    )


@app.get("/logs/stream", response_class=HTMLResponse)
def logs_stream(
    request: Request,
    filename: str = "",
    lines: int = _LOG_LINES_DEFAULT,
    level: str = "",
):
    if filename:
        safe = os.path.basename(filename)
        path = os.path.join(LOG_DIR, safe)
    else:
        path = _current_log_path()

    if not path or not os.path.isfile(path):
        return templates.TemplateResponse(
            "partials/log_stream.html",
            {"request": request, "lines": [], "filename": filename},
        )

    raw = _read_tail(path, lines)
    if level:
        raw = [l for l in raw if f" - {level.upper()} - " in l]

    return templates.TemplateResponse(
        "partials/log_stream.html",
        {"request": request, "lines": raw, "filename": os.path.basename(path)},
    )


@app.get("/logs/files", response_class=HTMLResponse)
def logs_files_partial(request: Request):
    return templates.TemplateResponse(
        "partials/log_files.html",
        {"request": request, "files": _log_files()},
    )


@app.get("/logs/{filename}/download")
def log_download(filename: str):
    safe = os.path.basename(filename)
    path = os.path.join(LOG_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="text/plain", filename=safe)


@app.delete("/logs/{filename}", response_class=HTMLResponse)
def log_delete(filename: str, request: Request):
    safe = os.path.basename(filename)
    path = os.path.join(LOG_DIR, safe)
    active = _current_log_path()
    if os.path.normpath(path) == os.path.normpath(active or ""):
        return templates.TemplateResponse(
            "partials/toast.html",
            {"request": request, "kind": "error", "message": "Cannot delete the active log file."},
        )
    try:
        os.remove(path)
    except OSError as exc:
        return templates.TemplateResponse(
            "partials/toast.html",
            {"request": request, "kind": "error", "message": str(exc)},
        )
    return templates.TemplateResponse(
        "partials/log_files.html",
        {"request": request, "files": _log_files()},
        headers={"HX-Trigger": "log-file-deleted"},
    )


@app.post("/logs/cleanup", response_class=HTMLResponse)
def log_cleanup(request: Request, db: Session = Depends(get_db)):
    retention = int(_get_log_setting(db, "log_retention_days", "7"))
    deleted = _cleanup_old_logs(retention)
    return templates.TemplateResponse(
        "partials/log_files.html",
        {"request": request, "files": _log_files()},
        headers={"HX-Trigger": json.dumps({"show-toast": {"kind": "success", "message": f"{deleted} log(s) deleted."}})},
    )


@app.post("/logs/settings", response_class=HTMLResponse)
async def log_settings_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    days = max(1, int(form.get("log_retention_days") or 7))
    _set_log_setting(db, "log_retention_days", str(days))
    return templates.TemplateResponse(
        "partials/toast.html",
        {"request": request, "kind": "success", "message": f"Retention set to {days} days."},
    )


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
