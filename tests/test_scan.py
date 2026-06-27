"""Direct test of the single-scan helper (the perf fix): one listdir must yield
both the cbz/cbr count and the issue-number set, identical to the old two-scan
path (_count_local_issues + _extract_nums)."""
import web.app as appmod
from web.models import Series


def test_scan_counts_and_extracts(db, comic_file):
    s = Series(publisher="Image", series_name="Saga", year=2012, metron_series_id=1)
    db.add(s)
    db.commit()
    db.refresh(s)
    comic_file(s, "Saga #001 (2012).cbz")
    comic_file(s, "Saga #002 (2012).cbr")
    comic_file(s, "Saga #1.5 (2012).cbz")   # decimal collapses to '1'
    comic_file(s, "Saga (no number).cbz")    # counted, no number extracted
    comic_file(s, "cover.jpg")               # ignored entirely

    count, nums = appmod._scan_series_dir(s)
    assert count == 4                # 4 cbz/cbr, jpg excluded
    assert nums == {"1", "2"}        # #001->1, #002->2, #1.5->1 (dupe), no-number skipped

    # equivalence with the original two helpers it replaced
    assert count == appmod._count_local_issues(s)
    assert nums == appmod._local_issue_numbers(s)


def test_scan_missing_folder(db):
    s = Series(publisher="Nope", series_name="Ghost", year=1999)
    db.add(s)
    db.commit()
    db.refresh(s)
    assert appmod._scan_series_dir(s) == (0, set())
