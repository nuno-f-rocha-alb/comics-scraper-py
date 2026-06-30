"""Reading lists (Phase A): parse Metron items, add (create series + monitor
only selected issue_types), per-item status, CBL export, Komga push payload.
All external boundaries mocked (metron_get fixture, temp DB/comics, fake Komga)."""
import xml.etree.ElementTree as ET

import web.app as appmod
from metadata.metron_reading_lists import parse_item
from web.cbl import build_cbl
from web.models import MonitoredIssue, ReadingList, ReadingListItem, Series, SuggestedReadingList
from web.reading_list_suggest import compute_coverage


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_item_maps_fields():
    # Real Metron /items/ shape: nested series has NO id, only name/volume/year_began.
    raw = {
        "order": 3, "issue_type": "Core Issue",
        "issue": {
            "id": 5432, "number": "1", "cover_date": "2015-07-01", "store_date": "2015-05-06",
            "cv_id": 123456, "series": {"name": "Secret Wars", "volume": 1, "year_began": 2015},
        },
    }
    p = parse_item(raw)
    assert p["metron_issue_id"] == 5432
    assert p["series_name"] == "Secret Wars"
    assert p["series_year"] == 2015
    assert p["series_volume"] == 1
    assert p["number"] == "1"
    assert p["cover_year"] == 2015
    assert p["cv_issue_id"] == 123456
    assert p["issue_type"] == "Core Issue"


# ── add: creates series + monitors only selected issue_types ──────────────────
def _series_detail(sid, name, pub="Image Comics", year=2024):
    return {
        "id": sid, "name": name, "year_began": year,
        "publisher": {"name": pub}, "issue_count": 12,
        "status": {"name": "Ongoing"}, "cv_id": 1000 + sid,
    }


def _item(order, itype, iid, num, name, year, cv):
    return {"order": order, "issue_type": itype,
            "issue": {"id": iid, "number": num, "cover_date": f"{year}-01-01", "cv_id": cv,
                      "series": {"name": name, "year_began": year, "volume": 1}}}


def _search(sid, year, volume=1):
    return {"results": [{"id": sid, "year_began": year, "volume": volume, "series": f"S ({year})"}]}


def test_add_creates_series_and_monitors_only_core(client, db, metron_get):
    detail = {"name": "My List", "list_type": "Event", "attribution_source": "Comic Book Herald"}
    items = {"next": None, "results": [
        _item(1, "Core Issue", 11, "1", "Absolute Batman", 2024, 901),
        _item(2, "Tie-In", 12, "1", "Absolute Flash", 2025, 902),
        _item(3, "Core Issue", 13, "2", "Absolute Batman", 2024, 903),
    ]}
    # No local series exist → each distinct series resolves via search→detail.
    # Call order: rl detail, items, Batman search, Batman detail, Flash search, Flash detail.
    metron_get([
        detail, items,
        _search(789, 2024), _series_detail(789, "Absolute Batman", pub="DC", year=2024),
        _search(790, 2025), _series_detail(790, "Absolute Flash", pub="DC", year=2025),
    ])

    r = client.post("/api/reading-lists", json={"metron_id": 42, "issue_types": ["Core Issue"]})
    assert r.status_code == 201, r.text
    assert r.json()["total"] == 3

    names = {s.series_name for s in db.query(Series).all()}
    assert {"Absolute Batman", "Absolute Flash"} <= names
    batman = db.query(Series).filter(Series.metron_series_id == 789).one()
    mon = {(m.series_id, m.issue_number) for m in db.query(MonitoredIssue).all()}
    assert (batman.id, "1") in mon and (batman.id, "2") in mon
    flash = db.query(Series).filter(Series.metron_series_id == 790).one()
    assert not any(sid == flash.id for sid, _ in mon)  # tie-in not monitored


def test_add_links_existing_local_series_without_metron(client, db, metron_get, comic_file):
    # The user already tracks the series AND owns the file → linked by name+year
    # with NO Metron series call, and the item reads as owned.
    s = Series(publisher="DC", series_name="Aquaman", year=2024)
    db.add(s); db.commit()
    comic_file(s, "Aquaman #011 (2024).cbz")

    detail = {"name": "DC List"}
    items = {"next": None, "results": [_item(1, "Core Issue", 50, "11", "Aquaman", 2024, 700)]}
    # Only the list detail + items are fetched — no series search/detail needed.
    metron_get([detail, items])

    r = client.post("/api/reading-lists", json={"metron_id": 9, "issue_types": None})
    assert r.status_code == 201
    body = r.json()
    assert body["owned"] == 1 and body["total"] == 1  # detected as owned via name+year

    detail_r = client.get(f"/api/reading-lists/{body['id']}").json()
    assert detail_r["items"][0]["status"] == "owned"


def test_add_monitors_all_when_no_types(client, db, metron_get):
    detail = {"name": "All List"}
    items = {"next": None, "results": [_item(1, "Tie-In", 21, "1", "Some Series", 2024, 600)]}
    metron_get([detail, items, _search(555, 2024), _series_detail(555, "Some Series", year=2024)])
    r = client.post("/api/reading-lists", json={"metron_id": 7, "issue_types": None})
    assert r.status_code == 201
    assert db.query(MonitoredIssue).count() == 1  # tie-in monitored because no filter


def test_add_dedupes_same_series_number_no_unique_violation(client, db, metron_get):
    # Two list items for the SAME series + number (e.g. multiple printings) must
    # produce one MonitoredIssue, not collide on the unique constraint at commit
    # (regression: session is autoflush=False, so the dedup query couldn't see
    # the prior iteration's pending row).
    detail = {"name": "Dupe List"}
    items = {"next": None, "results": [
        _item(1, "Core Issue", 11, "1", "Book of Butcher", 2024, 901),
        _item(2, "Core Issue", 12, "1", "Book of Butcher", 2024, 902),
    ]}
    # Same series resolves once (series_cache) → single search + detail.
    metron_get([detail, items, _search(71, 2024), _series_detail(71, "Book of Butcher", year=2024)])

    r = client.post("/api/reading-lists", json={"metron_id": 99, "issue_types": ["Core Issue"]})
    assert r.status_code == 201, r.text
    assert r.json()["total"] == 2
    assert db.query(MonitoredIssue).count() == 1


def test_add_does_not_crash_when_issue_already_monitored(client, db, comic_file, metron_get):
    # A prior list/run already monitors this series' #1 (committed). Adding a list
    # that includes it must not raise a unique-constraint error and poison the txn.
    s = Series(publisher="DC", series_name="Pre Owned", year=2024)
    db.add(s); db.commit()
    db.add(MonitoredIssue(series_id=s.id, issue_number="1", issue_type="regular"))
    db.commit()

    detail = {"name": "Pre List"}
    items = {"next": None, "results": [_item(1, "Core Issue", 60, "1", "Pre Owned", 2024, 700)]}
    metron_get([detail, items])  # local series found by name+year → no Metron series call

    r = client.post("/api/reading-lists", json={"metron_id": 77, "issue_types": ["Core Issue"]})
    assert r.status_code == 201, r.text
    assert db.query(MonitoredIssue).count() == 1  # still one — no duplicate inserted


def test_monitor_none_survives_resync(client, db, metron_get):
    # issue_types=[] (monitor none) must not degrade to "all" on re-sync.
    s = Series(publisher="DC", series_name="Aquaman", year=2024)
    db.add(s); db.commit()
    items = {"next": None, "results": [_item(1, "Core Issue", 50, "11", "Aquaman", 2024, 700)]}

    metron_get([{"name": "L"}, items])  # series links locally → no series Metron call
    r = client.post("/api/reading-lists", json={"metron_id": 9, "issue_types": []})
    assert r.status_code == 201
    assert db.query(MonitoredIssue).count() == 0
    rl = db.query(ReadingList).filter_by(metron_id=9).one()
    assert rl.monitored_issue_types != ""  # stored as the 'none' sentinel, not 'all'

    metron_get([{"name": "L"}, items])  # resync re-fetches detail + items
    r2 = client.post(f"/api/reading-lists/{rl.id}/resync")
    assert r2.status_code == 200
    assert db.query(MonitoredIssue).count() == 0  # still none — didn't flip to all


# ── per-item status ──────────────────────────────────────────────────────────
def test_item_status(client, db, comic_file):
    s = Series(publisher="Image Comics", series_name="Saga", year=2012, metron_series_id=101)
    db.add(s); db.flush()
    rl = ReadingList(metron_id=1, name="L", num_items=3)
    db.add(rl); db.flush()
    db.add(MonitoredIssue(series_id=s.id, issue_number="2", issue_type="regular"))
    db.add(ReadingListItem(reading_list_id=rl.id, order=1, metron_issue_id=1, metron_series_id=101,
                           series_name="Saga", number="1", series_id=s.id))   # will have a file → owned
    db.add(ReadingListItem(reading_list_id=rl.id, order=2, metron_issue_id=2, metron_series_id=101,
                           series_name="Saga", number="2", series_id=s.id))   # monitored, no file
    db.add(ReadingListItem(reading_list_id=rl.id, order=3, metron_issue_id=3, metron_series_id=101,
                           series_name="Saga", number="3", series_id=s.id))   # missing
    db.commit()
    comic_file(s, "Saga #001 (2012).cbz")

    r = client.get(f"/api/reading-lists/{rl.id}")
    statuses = {it["number"]: it["status"] for it in r.json()["items"]}
    assert statuses == {"1": "owned", "2": "monitored", "3": "missing"}


# ── CBL ──────────────────────────────────────────────────────────────────────
def test_build_cbl_structure():
    items = [
        ReadingListItem(reading_list_id=1, order=1, metron_issue_id=1, series_name="Absolute Batman",
                        number="1", series_year=2024, cover_year=2024, cv_series_id=160294, cv_issue_id=1073108),
        ReadingListItem(reading_list_id=1, order=2, metron_issue_id=2, series_name="Absolute Flash",
                        number="2", series_year=2025, cover_year=2025),  # no cv ids → no <Database>
    ]
    xml = build_cbl("My & List", items)
    root = ET.fromstring(xml)
    assert root.find("Name").text == "My & List"
    assert root.find("NumIssues").text == "2"
    books = root.find("Books").findall("Book")
    assert books[0].get("Series") == "Absolute Batman"
    assert books[0].get("Volume") == "2024" and books[0].get("Year") == "2024"
    assert books[0].find("Database").get("Issue") == "1073108"
    assert books[1].find("Database") is None  # omitted when cv ids absent


# ── Komga push ───────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.content = data, status, b"x"
    def raise_for_status(self): pass
    def json(self): return self._data


class _FakeSession:
    def __init__(self):
        self.headers = {}
        self.posted = None
    def get(self, url, params=None, timeout=None):
        if "/series/" in url and "/books" in url:
            return _FakeResp({"content": [{"id": "bk1", "metadata": {"number": "1"}}]})
        if url.endswith("/series"):
            return _FakeResp({"content": [{"id": "se1", "metadata": {"title": params["search"]}}]})
        if url.endswith("/readlists"):
            return _FakeResp({"content": []})  # none existing
        return _FakeResp({"content": []})
    def post(self, url, json=None, timeout=None):
        self.posted = json
        return _FakeResp({"id": "rl-new"})


# ── Phase B: suggestions ─────────────────────────────────────────────────────
def test_compute_coverage():
    # owned_map keyed by (normalized series_name, year) — Metron items have no series id.
    owned = {("saga", 2012): {"1", "2"}, ("daredevil", 2022): {"5"}}
    items = [
        {"series_name": "Saga", "series_year": 2012, "number": "1"},      # owned
        {"series_name": "Saga", "series_year": 2012, "number": "3"},       # tracked, not owned
        {"series_name": "Daredevil", "series_year": 2022, "number": "5"},  # owned
        {"series_name": "Unknown", "series_year": 1999, "number": "1"},    # untracked
    ]
    assert compute_coverage(items, owned) == (2, 4)


def test_suggestions_filter_and_exclude_added(client, db):
    # threshold defaults to 50%
    db.add(SuggestedReadingList(metron_id=1, name="High", owned=6, total=10, coverage=0.6))
    db.add(SuggestedReadingList(metron_id=2, name="Low", owned=3, total=10, coverage=0.3))   # below 50%
    db.add(SuggestedReadingList(metron_id=3, name="Added", owned=8, total=10, coverage=0.8))
    db.add(ReadingList(metron_id=3, name="Added", num_items=10))  # already added → excluded
    db.commit()

    r = client.get("/api/reading-list-suggestions")
    names = [s["name"] for s in r.json()["suggestions"]]
    assert names == ["High"]  # Low filtered by threshold, Added excluded
    assert r.json()["suggestions"][0]["coverage"] == 60


def test_suggestion_threshold_setting(client, db):
    db.add(SuggestedReadingList(metron_id=2, name="Low", owned=3, total=10, coverage=0.3))
    db.commit()
    client.put("/api/reading-list-suggestions/settings", json={"threshold": 25})
    names = [s["name"] for s in client.get("/api/reading-list-suggestions").json()["suggestions"]]
    assert names == ["Low"]  # now above the lowered 25% threshold


def test_nightly_komga_repush(db, monkeypatch):
    import web.scheduler as sched
    import web.komga_client as kc
    import web.app as appmod
    rl = ReadingList(metron_id=1, name="L", num_items=1)
    db.add(rl); db.commit()

    monkeypatch.setattr(kc, "is_configured", lambda: True)
    pushed: list[str] = []
    monkeypatch.setattr(appmod, "_push_reading_list_komga",
                        lambda r, d: pushed.append(r.name) or {"matched": 1, "unmatched": []})

    sched._wrapped_komga_nightly()
    assert pushed == ["L"]


def test_nightly_komga_noop_when_unconfigured(db, monkeypatch):
    import web.scheduler as sched
    import web.komga_client as kc
    import web.app as appmod
    db.add(ReadingList(metron_id=2, name="X", num_items=1)); db.commit()
    monkeypatch.setattr(kc, "is_configured", lambda: False)
    called = {"n": 0}
    monkeypatch.setattr(appmod, "_push_reading_list_komga", lambda r, d: called.__setitem__("n", called["n"] + 1))
    sched._wrapped_komga_nightly()
    assert called["n"] == 0  # never touches Komga when not configured


def test_overview_exposes_ended_flag(client, db):
    db.add(Series(publisher="DC", series_name="Watchmen", year=1986, status="Completed"))
    db.add(Series(publisher="Image", series_name="Saga", year=2012, status="Ongoing"))
    db.commit()
    cards = {c["series_name"]: c for c in client.get("/api/series/overview").json()["series"]}
    assert cards["Watchmen"]["ended"] is True
    assert cards["Saga"]["ended"] is False


def test_komga_push_builds_ordered_payload(monkeypatch):
    from web import komga_client
    monkeypatch.setattr(komga_client, "KOMGA_URL", "http://komga:25600")
    monkeypatch.setattr(komga_client, "KOMGA_API_KEY", "key")
    fake = _FakeSession()
    monkeypatch.setattr(komga_client, "_session", lambda: fake)

    res = komga_client.push_reading_list(
        "List", "summary",
        [("Absolute Batman", "1"), ("Absolute Flash", "99")],  # #99 won't match
    )
    assert res["created"] is True
    assert res["matched"] == 1
    assert res["unmatched"] == ["Absolute Flash #99"]
    assert fake.posted["bookIds"] == ["bk1"]
    assert fake.posted["ordered"] is True
