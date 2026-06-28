"""Auto-pause coverage must account for monitored annuals, not just regular
issues — an ended series with an undownloaded monitored annual stays enabled."""
import os

import web.app as appmod
from web.models import MonitoredIssue, Series


def _ended(db, **kw):
    kw.setdefault("enabled", True)
    s = Series(publisher="Image", series_name="Spawn", year=1992,
               status="Completed", total_issues=3, **kw)
    db.add(s)
    db.commit()
    return s


def _regular(db, s, *nums):
    for n in nums:
        db.add(MonitoredIssue(series_id=s.id, issue_number=str(n), issue_type="regular"))
    db.commit()


def _annual(db, s, *nums):
    for n in nums:
        db.add(MonitoredIssue(series_id=s.id, issue_number=str(n), issue_type="annual"))
    db.commit()


def _annual_file(s, name):
    folder = os.path.join(appmod._series_dir(s), "Annuals")
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, name), "w").write("x")


def test_monitored_annual_missing_keeps_series_enabled(db, comic_file):
    s = _ended(db)
    _regular(db, s, 1, 2, 3)
    _annual(db, s, 1)
    for n in (1, 2, 3):
        comic_file(s, f"Spawn #{n} (1992).cbz")
    # Annual #1 monitored but no local file → not complete.

    appmod._recompute_pause_state(s, db)

    assert s.enabled is True


def test_all_monitored_files_present_pauses(db, comic_file):
    s = _ended(db)
    _regular(db, s, 1, 2, 3)
    _annual(db, s, 1)
    for n in (1, 2, 3):
        comic_file(s, f"Spawn #{n} (1992).cbz")
    _annual_file(s, "Spawn Annual #1 (1992).cbz")

    appmod._recompute_pause_state(s, db)

    assert s.enabled is False
