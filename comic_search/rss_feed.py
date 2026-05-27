"""getcomics.org RSS feed consumer.

Reads the WordPress RSS 2.0 feed at https://getcomics.org/feed/ and parses
each <item> into a dict the rest of the app can match against monitored
series. Single HTTP request gets us the last ~10 posts — much lighter on
the source than the per-series search the scraper currently fans out.

Future direction: this module is the seed of the Sonarr/Radarr-style
feed-driven monitoring model (see project_architecture_direction memory).
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

from config import HEADERS

DEFAULT_FEED_URL = "https://getcomics.org/feed/"

log = logging.getLogger(__name__)


@dataclass
class FeedEntry:
    title: str            # raw post title, HTML entities decoded
    url: str              # getcomics post URL
    pub_date: datetime | None
    categories: list[str]
    description: str

    # Parsed from title — None if the title doesn't follow "Series #N (YYYY)"
    series_name: str | None = None
    issue_number: str | None = None
    year: int | None = None


_TITLE_RE = re.compile(
    r"^(?P<name>.*?)\s*#(?P<number>\d+(?:\.\d+)?)\s*\((?P<year>\d{4})\)\s*$"
)


def _parse_title(title: str) -> tuple[str | None, str | None, int | None]:
    """Split a title like 'Series Name #5 (2026)' into (name, number, year).

    Returns (None, None, None) if the title doesn't match — e.g. TPBs,
    one-shots phrased without #N, omnibus collections.
    """
    m = _TITLE_RE.match(title)
    if not m:
        return None, None, None
    return m.group("name").strip(), m.group("number"), int(m.group("year"))


def fetch_feed(url: str = DEFAULT_FEED_URL, timeout: int = 10) -> list[FeedEntry]:
    """Fetch and parse the RSS feed. Returns entries in feed order (newest first)."""
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    channel = root.find("channel")
    if channel is None:
        log.warning("Feed has no <channel>: %s", url)
        return []

    entries: list[FeedEntry] = []
    for item in channel.findall("item"):
        title_raw = (item.findtext("title") or "").strip()
        title = html.unescape(title_raw).replace("–", "-")  # en-dash → hyphen
        url_ = (item.findtext("link") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        try:
            pub_date = parsedate_to_datetime(pub_raw) if pub_raw else None
        except (TypeError, ValueError):
            pub_date = None
        categories = [c.text.strip() for c in item.findall("category") if c.text]
        desc = (item.findtext("description") or "").strip()

        name, num, year = _parse_title(title)
        entries.append(FeedEntry(
            title=title, url=url_, pub_date=pub_date,
            categories=categories, description=desc,
            series_name=name, issue_number=num, year=year,
        ))
    return entries
