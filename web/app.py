import os
import re
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from web.database import SessionLocal, init_db
from web.models import Series

COMICS_BASE_DIR = os.getenv("COMICS_BASE_DIR", "/app/comics")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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


def _count_local_issues(s: Series) -> int:
    path = os.path.join(COMICS_BASE_DIR, s.publisher, f"{s.series_name} ({s.year})")
    if not os.path.isdir(path):
        return 0
    return sum(1 for f in os.listdir(path) if f.lower().endswith((".cbz", ".cbr")))


def _local_issue_numbers(s: Series) -> set[str]:
    path = os.path.join(COMICS_BASE_DIR, s.publisher, f"{s.series_name} ({s.year})")
    if not os.path.isdir(path):
        return set()
    nums: set[str] = set()
    for f in os.listdir(path):
        if f.lower().endswith((".cbz", ".cbr")):
            m = re.search(r"#(\d+(?:\.\d+)?)", f)
            if m:
                try:
                    nums.add(str(int(float(m.group(1)))))
                except ValueError:
                    pass
    return nums


def _fetch_metron_issues(metron_series_id: int) -> list[dict]:
    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get

    issues: list[dict] = []
    url = f"{METRON_BASE_URL}/issue/?series_id={metron_series_id}&ordering=number"
    while url:
        r = metron_get(url)
        data = r.json()
        issues.extend(data.get("results", []))
        url = data.get("next")
    return issues


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
            "annual_comicvine_volume_id": s.annual_comicvine_volume_id,
            "getcomics_search_name": s.getcomics_search_name,
            "enabled": s.enabled,
            "cover_image_url": s.cover_image_url,
            "total_issues": s.total_issues,
        }
        for s in rows
    ]


# ── Metron search proxy (HTMX partials) ───────────────────────────────────────

@app.get("/api/metron/search", response_class=HTMLResponse)
def metron_search(request: Request, name: str = ""):
    if len(name.strip()) < 2:
        return HTMLResponse("")
    try:
        from config import METRON_BASE_URL
        from metadata.metron_client import get as metron_get
        r = metron_get(f"{METRON_BASE_URL}/series/", name=name.strip())
        results = r.json().get("results", [])
        return templates.TemplateResponse(
            "partials/metron_results.html",
            {"request": request, "results": results, "query": name},
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger mt-2">Metron search error: {exc}</div>'
        )


@app.get("/api/metron/series/{metron_id}/add-form", response_class=HTMLResponse)
def metron_series_add_form(request: Request, metron_id: int):
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


# ── Cover sync ─────────────────────────────────────────────────────────────────

@app.post("/api/sync-covers")
def sync_covers(db: Session = Depends(get_db)):
    from config import METRON_BASE_URL
    from metadata.metron_client import get as metron_get

    rows = db.query(Series).filter(
        Series.metron_series_id.isnot(None),
        Series.cover_image_url.is_(None),
    ).all()

    updated = 0
    for s in rows:
        try:
            r = metron_get(f"{METRON_BASE_URL}/series/{s.metron_series_id}/")
            data = r.json()
            s.cover_image_url = data.get("image") or None
            s.total_issues = data.get("issue_count") or None
            updated += 1
        except Exception:
            pass

    db.commit()
    return RedirectResponse(
        url=f"/series?msg=Covers synced for {updated} series.",
        status_code=303,
    )


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
    annual_comicvine_volume_id: str = Form(""),
    getcomics_search_name: str = Form(""),
    cover_image_url: str = Form(""),
    total_issues: str = Form(""),
):
    s = Series(
        publisher=publisher.strip(),
        series_name=series_name.strip(),
        year=int(year) if year.strip() else None,
        comicvine_volume_id=int(comicvine_volume_id) if comicvine_volume_id.strip() else None,
        metron_series_id=int(metron_series_id) if metron_series_id.strip() else None,
        annual_comicvine_volume_id=int(annual_comicvine_volume_id) if annual_comicvine_volume_id.strip() else None,
        getcomics_search_name=getcomics_search_name.strip() or None,
        cover_image_url=cover_image_url.strip() or None,
        total_issues=int(total_issues) if total_issues.strip() else None,
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
    annual_comicvine_volume_id: str = Form(""),
    getcomics_search_name: str = Form(""),
):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)
    s.publisher = publisher.strip()
    s.series_name = series_name.strip()
    s.year = int(year) if year.strip() else None
    s.comicvine_volume_id = int(comicvine_volume_id) if comicvine_volume_id.strip() else None
    s.metron_series_id = int(metron_series_id) if metron_series_id.strip() else None
    s.annual_comicvine_volume_id = int(annual_comicvine_volume_id) if annual_comicvine_volume_id.strip() else None
    s.getcomics_search_name = getcomics_search_name.strip() or None
    db.commit()
    return RedirectResponse(
        url=f"/series?msg={quote(s.series_name + ' updated successfully.')}",
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
def series_issues_partial(request: Request, series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if not s:
        raise HTTPException(status_code=404)

    if not s.metron_series_id:
        return HTMLResponse(
            '<div class="alert alert-info m-3">No Metron ID set — issue list unavailable.</div>'
        )

    local_nums = _local_issue_numbers(s)
    issues: list[dict] = []

    try:
        raw = _fetch_metron_issues(s.metron_series_id)
        today = date.today()
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

            name_raw = issue.get("issue_name") or issue.get("name") or ""
            title = ", ".join(name_raw) if isinstance(name_raw, list) else name_raw

            issues.append({
                "number": num_str,
                "title": title,
                "date": str(date_str)[:10] if date_str else "",
                "cover": cover,
                "status": status,
            })
    except Exception as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger m-3">Error fetching issues from Metron: {exc}</div>'
        )

    return templates.TemplateResponse(
        "partials/series_issues.html",
        {"request": request, "issues": issues},
    )
