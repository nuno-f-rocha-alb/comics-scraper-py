"""Regression tests for the app-findings-fix flow unit:
  R1 — issue-number normalization is decimal-safe and never crashes on non-numeric.
  R2 — RSS feed matching disambiguates same-named series by year, never guesses.
All network/DB mocked via conftest fixtures."""
import web.app as appmod
from comic_search.rss_feed import FeedEntry
from web.models import Series


def _fe(name, num, year):
    return FeedEntry(
        title=f"{name} #{num} ({year})", url=f"https://getcomics.org/comic/{name}-{num}",
        pub_date=None, categories=[], description="",
        series_name=name, issue_number=str(num), year=year,
    )


def _series(db, name, year):
    s = Series(publisher="DC", series_name=name, year=year, enabled=True)
    db.add(s)
    db.commit()
    return s


# ── R1 ────────────────────────────────────────────────────────────────────────

def test_find_issue_file_non_numeric_returns_none(db, comic_file):
    """Non-numeric route input must not raise (was: int(float('abc')) → 500)."""
    s = _series(db, "Batman", 2016)
    comic_file(s, "Batman #1 (2016).cbz")
    assert appmod._find_issue_file(s, "not-a-number") is None


def test_find_issue_file_keeps_decimal_distinct(db, comic_file):
    """#1.5 must not collapse onto #1."""
    s = _series(db, "Saga", 2012)
    comic_file(s, "Saga #1 (2012).cbz")
    comic_file(s, "Saga #1.5 (2012).cbz")
    one = appmod._find_issue_file(s, "1")
    half = appmod._find_issue_file(s, "1.5")
    assert one and half and one != half
    assert one.endswith("Saga #1 (2012).cbz")
    assert half.endswith("Saga #1.5 (2012).cbz")


def test_local_issue_numbers_preserves_decimal(db, comic_file):
    s = _series(db, "Saga", 2012)
    comic_file(s, "Saga #1 (2012).cbz")
    comic_file(s, "Saga #1.5 (2012).cbz")
    assert appmod._local_issue_numbers(s) == {"1", "1.5"}


# ── R2 ────────────────────────────────────────────────────────────────────────

def test_rss_two_volumes_binds_by_year(db):
    old = _series(db, "Teen Titans", 2003)
    new = _series(db, "Teen Titans", 2016)
    matches = appmod._match_feed_entries([_fe("Teen Titans", 5, 2016)], db)
    assert len(matches) == 1
    assert matches[0]["series"].id == new.id  # not the last-inserted / arbitrary one


def test_rss_ambiguous_year_skips(db):
    # Two enabled same-name volumes, feed year matches neither → skip, don't guess.
    _series(db, "Teen Titans", 2003)
    _series(db, "Teen Titans", 2016)
    matches = appmod._match_feed_entries([_fe("Teen Titans", 5, 1999)], db)
    assert matches == []


def test_rss_single_volume_still_matches(db):
    s = _series(db, "Teen Titans", 2016)
    matches = appmod._match_feed_entries([_fe("Teen Titans", 5, 2016)], db)
    assert len(matches) == 1 and matches[0]["series"].id == s.id
