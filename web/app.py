import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from urllib.parse import quote

log = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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

        date_str = issue.get("cover_date") or issue.get("store_date") or ""
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
    metron_series_id: int, db: Session, *, force: bool = False, block: bool = True
) -> list[dict]:
    """Return issues from local cache (if fresh) else fetch from Metron and store."""
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
        if not cached_name:
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

        # Phase 2: fetch cover + issue count from Metron detail
        if s.metron_series_id and not s.cover_image_url:
            try:
                r = metron_get(f"{METRON_BASE_URL}/series/{s.metron_series_id}/")
                data = r.json()
                img = data.get("image") or ""
                s.cover_image_url = img if isinstance(img, str) and img else None
                s.total_issues = data.get("issue_count") or None
                # Backfill CV ID if missing
                if not s.comicvine_volume_id and data.get("cv_id"):
                    s.comicvine_volume_id = data["cv_id"]
                # Fallback: use first issue cover if series has no image
                if not s.cover_image_url:
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
                updated_covers += 1
            except Exception:
                pass

    db.commit()
    parts = []
    if found_ids:
        parts.append(f"{found_ids} Metron IDs found via CV ID")
    if updated_covers:
        parts.append(f"{updated_covers} covers synced")
    msg = ", ".join(parts) if parts else "Nothing to update."
    return RedirectResponse(url=f"/series?msg={quote(msg)}", status_code=303)


# ── Verify search ──────────────────────────────────────────────────────────────

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
        import requests as req_lib
        from bs4 import BeautifulSoup
        from config import BASE_SEARCH_URL, HEADERS

        url = f"{BASE_SEARCH_URL.format(1)}{search_term.replace(' ', '+')}"
        r = req_lib.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 404 or "No Results Found" in r.text:
            comics = []
        else:
            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.select("div.post-info h1.post-title a")
            comics = [{"title": a.get_text(strip=True), "url": a["href"]} for a in links[:10]]
        return templates.TemplateResponse(
            "partials/verify_results.html",
            {"request": request, "comics": comics, "search_term": search_term},
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger mt-2 py-2 small">Verify error: {exc}</div>'
        )


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
    db.commit()
    return Response(status_code=200, headers={"HX-Trigger": "refresh-issues"})


@app.delete("/series/{series_id}/monitor-all")
def unmonitor_all(series_id: int, db: Session = Depends(get_db)):
    db.query(MonitoredIssue).filter(MonitoredIssue.series_id == series_id).delete()
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
        db.commit()
        monitored = False
    else:
        db.add(MonitoredIssue(series_id=series_id, issue_number=norm, issue_type=issue_type))
        db.commit()
        monitored = True

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
    active = (
        db.query(DownloadJob)
        .filter(DownloadJob.status.in_(["queued", "downloading"]))
        .order_by(DownloadJob.created_at)
        .all()
    )
    series_map = {s.id: s for s in db.query(Series).all()}
    return templates.TemplateResponse(
        "partials/downloads_active.html",
        {"request": request, "jobs": active, "series_map": series_map},
    )


@app.delete("/downloads/{job_id}")
def download_delete(job_id: int, db: Session = Depends(get_db)):
    job = db.get(DownloadJob, job_id)
    if job and job.status not in ("queued", "downloading"):
        db.delete(job)
        db.commit()
    return Response(status_code=200)


@app.delete("/downloads", response_class=HTMLResponse)
def downloads_clear(db: Session = Depends(get_db)):
    db.query(DownloadJob).filter(
        DownloadJob.status.in_(["done", "failed"])
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


# ── HTML pages ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/series")


@app.get("/series", response_class=HTMLResponse)
def series_list(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Series).order_by(Series.publisher, Series.series_name).all()
    local_counts = {s.id: _count_local_issues(s) for s in rows}
    return templates.TemplateResponse(
        "series_list.html", {"request": request, "series": rows, "local_counts": local_counts}
    )


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
            if not os.path.exists(src):
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
