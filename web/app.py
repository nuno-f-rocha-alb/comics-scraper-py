from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from web.database import SessionLocal, init_db
from web.models import Series


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Comics Scraper", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


def get_db():
    with SessionLocal() as db:
        yield db


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


# ── HTML pages ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/series")


@app.get("/series", response_class=HTMLResponse)
def series_list(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Series).order_by(Series.publisher, Series.series_name).all()
    return templates.TemplateResponse(
        "series_list.html", {"request": request, "series": rows}
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
):
    s = Series(
        publisher=publisher.strip(),
        series_name=series_name.strip(),
        year=int(year) if year.strip() else None,
        comicvine_volume_id=int(comicvine_volume_id) if comicvine_volume_id.strip() else None,
        metron_series_id=int(metron_series_id) if metron_series_id.strip() else None,
        annual_comicvine_volume_id=int(annual_comicvine_volume_id) if annual_comicvine_volume_id.strip() else None,
        getcomics_search_name=getcomics_search_name.strip() or None,
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


@app.delete("/series/{series_id}")
def series_delete(series_id: int, db: Session = Depends(get_db)):
    s = db.query(Series).filter(Series.id == series_id).first()
    if s:
        db.delete(s)
        db.commit()
    return Response(status_code=200)
