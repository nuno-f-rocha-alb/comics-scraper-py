"""Log viewer filters: the `%(name)s` field added to the format must not break
level detection (the `- LEVEL -` substring), and category keywords must match
both named loggers and root-logger scraper lines."""
from web.app import _LOG_CATEGORIES, _classify_log_line

# A line in the current format: asctime - LEVEL - name - message
_ERR = "2026-06-30 03:00:00,000 - ERROR - metadata.metron_client - boom"
_INFO = "2026-06-30 03:00:00,000 - INFO - comic_search.rss_monitor - 3 new issues"
_SCRAPE = "2026-06-30 03:00:00,000 - INFO - root - Downloading Batman #1.cbz"


def _level_match(line, level):  # mirrors api_logs_stream
    return f" - {level.upper()} - " in line


def _cat_match(line, category):  # mirrors api_logs_stream
    kws = _LOG_CATEGORIES.get(category.lower())
    return bool(kws) and any(k in line.lower() for k in kws)


def test_name_field_does_not_break_level_filter():
    assert _level_match(_ERR, "ERROR")
    assert not _level_match(_INFO, "ERROR")
    assert _classify_log_line(_ERR) == "error"


def test_category_matches_named_logger_by_name():
    assert _cat_match(_INFO, "rss")
    assert _cat_match(_ERR, "metron")
    assert not _cat_match(_INFO, "metron")


def test_category_matches_root_scraper_line_by_keyword():
    assert _cat_match(_SCRAPE, "scraper")
    assert not _cat_match(_SCRAPE, "komga")
