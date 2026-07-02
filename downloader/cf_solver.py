"""FlareSolverr client — solves Cloudflare "are you human" challenges.

Enabled only when config.FLARESOLVERR_URL is set; otherwise every helper is a
no-op and callers fall back to plain requests. A solve returns the page HTML plus
the CF clearance (cookies + user-agent), which is cached per host so the streaming
download in download_file.py can replay it (FlareSolverr can't stream large files).
"""
from config import *
from urllib.parse import urlparse
import requests

# ponytail: per-host clearance cache lives only for the process; a worker restart
# wipes it and a stale cf_clearance cookie just triggers a re-solve. No TTL needed.
_clearance = {}  # host -> {"cookies": {name: value}, "ua": str}


def _enabled():
    return bool(FLARESOLVERR_URL)


def _host(url):
    return urlparse(url).netloc


def solve(url):
    """Solve `url` via FlareSolverr. Returns the parsed solution dict
    {"html", "cookies", "ua"} and caches clearance for the host, or None on any
    failure/timeout (logged as one WARNING line — a miss is an expected skip)."""
    if not _enabled():
        return None
    payload = {"cmd": "request.get", "url": url, "maxTimeout": CF_SOLVER_TIMEOUT}
    if PROXY_URL:  # ponytail: single optional proxy, no rotation
        payload["proxy"] = {"url": PROXY_URL}
    try:
        # timeout > maxTimeout so FlareSolverr's own timeout fires first
        resp = requests.post(f"{FLARESOLVERR_URL}/v1", json=payload,
                             timeout=CF_SOLVER_TIMEOUT / 1000 + 15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            logging.warning(f"FlareSolverr could not solve {url}: {data.get('message')}")
            return None
        sol = data["solution"]
        cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
        ua = sol.get("userAgent", "")
        _clearance[_host(url)] = {"cookies": cookies, "ua": ua}
        return {"html": sol.get("response", ""), "cookies": cookies, "ua": ua}
    except Exception as e:
        logging.warning(f"FlareSolverr request failed for {url}: {e}")
        return None


def get_page(url):
    """Solved HTML for `url`, or None (disabled or unsolvable)."""
    sol = solve(url)
    return sol["html"] if sol else None


def clearance_for(url):
    """(cookies_dict, ua) for the URL's host — solving once and caching if needed.
    (None, None) when disabled or unsolvable. cf_clearance is host-scoped + UA-bound,
    so a gated mirror is solved on its own host before its cookies are replayed."""
    if not _enabled():
        return None, None
    cached = _clearance.get(_host(url))
    if cached is None:
        solve(url)
        cached = _clearance.get(_host(url))
    if cached:
        return cached["cookies"], cached["ua"]
    return None, None
