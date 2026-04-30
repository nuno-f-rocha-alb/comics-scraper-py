import time
import logging
import requests
from config import METRON_BASE_URL, METRON_USER, METRON_PASS, HEADERS_APP

_session = requests.Session()
_session.auth = (METRON_USER, METRON_PASS)
_session.headers.update(HEADERS_APP)


def _check_burst(response):
    remaining = response.headers.get("X-RateLimit-Burst-Remaining")
    if remaining is not None and int(remaining) <= 1:
        reset_ts = int(response.headers.get("X-RateLimit-Burst-Reset", time.time() + 60))
        wait = max(reset_ts - time.time(), 60)
        logging.info(f"Metron burst limit reached, pausing {wait:.0f}s")
        time.sleep(wait)


def get(url, **params):
    """GET request to Metron with rate limit handling. Raises on non-2xx (except 304)."""
    r = _session.get(url, params=params or None, timeout=15)
    if r.status_code == 429:
        retry_after = int(r.headers.get("Retry-After", 60))
        wait = max(retry_after, 60)
        logging.warning(f"Metron rate limited (429), waiting {wait}s")
        time.sleep(wait)
        r = _session.get(url, params=params or None, timeout=15)
    if r.status_code != 304:
        r.raise_for_status()
        _check_burst(r)
    return r
