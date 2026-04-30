import time
import logging
import requests
from config import METRON_BASE_URL, METRON_USER, METRON_PASS, HEADERS_APP

_session = requests.Session()
_session.auth = (METRON_USER, METRON_PASS)
_session.headers.update(HEADERS_APP)

_available_after: float = 0  # epoch time when the rate limit window resets


class RateLimitedError(Exception):
    def __init__(self, seconds: float):
        self.seconds = seconds
        super().__init__(f"Metron rate limited, available in {seconds:.0f}s")


def seconds_remaining() -> float:
    return max(0.0, _available_after - time.time())


def _set_cooldown(seconds: float) -> None:
    global _available_after
    _available_after = time.time() + seconds


def _check_burst(response, block: bool = True):
    remaining = response.headers.get("X-RateLimit-Burst-Remaining")
    if remaining is not None and int(remaining) <= 1:
        reset_ts = int(response.headers.get("X-RateLimit-Burst-Reset", time.time() + 60))
        wait = max(reset_ts - time.time(), 60)
        _set_cooldown(wait)
        logging.info(f"Metron burst limit reached, pausing {wait:.0f}s")
        if block:
            time.sleep(wait)


def get(url, *, block: bool = True, **params):
    """GET request to Metron with rate limit handling.

    block=True  (default): sleep until rate limit clears — use in background jobs.
    block=False : raise RateLimitedError immediately — use in web request handlers.
    """
    rem = seconds_remaining()
    if rem > 0:
        if not block:
            raise RateLimitedError(rem)
        logging.info(f"Metron in cooldown, waiting {rem:.0f}s")
        time.sleep(rem)

    r = _session.get(url, params=params or None, timeout=15)
    if r.status_code == 429:
        retry_after = int(r.headers.get("Retry-After", 60))
        wait = max(retry_after, 60)
        _set_cooldown(wait)
        logging.warning(f"Metron rate limited (429), waiting {wait}s")
        if not block:
            raise RateLimitedError(wait)
        time.sleep(wait)
        r = _session.get(url, params=params or None, timeout=15)

    if r.status_code != 304:
        r.raise_for_status()
        _check_burst(r, block=block)
    return r
