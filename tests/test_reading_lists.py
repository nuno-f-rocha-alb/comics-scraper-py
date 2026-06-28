"""Reading lists (Phase A): parse Metron items, add (create series + monitor
only selected issue_types), per-item status, CBL export, Komga push payload.
All external boundaries mocked (metron_get fixture, temp DB/comics, fake Komga)."""
import xml.etree.ElementTree as ET

import web.app as appmod
from metadata.metron_reading_lists import parse_item
from web.cbl import build_cbl
from web.models import MonitoredIssue, ReadingList, ReadingListItem, Series


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_item_maps_fields():
    raw = {
        "order": 3, "issue_type": "Core Issue",
        "issue": {
            "id": 5432, "number": "1", "cover_date": "2015-07-01", "store_date": "2015-05-06",
            "cv_id": 123456, "series": {"id": 789, "name": "Secret Wars", "volume": 1},
        },
    }
    p = parse_item(raw)
    assert p["metron_issue_id"] == 5432
    assert p["metron_series_id"] == 789
    assert p["series_name"] == "Secret Wars"
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


def test_add_creates_series_and_monitors_only_core(client, db, metron_get):
    detail = {"name": "My List", "list_type": "Event", "attribution_source": "Comic Book Herald"}
    items = {"next": None, "results": [
        {"order": 1, "issue_type": "Core Issue",
         "issue": {"id": 11, "number": "1", "cover_date": "2024-01-01", "cv_id": 901,
                   "series": {"id": 789, "name": "Absolute Batman"}}},
        {"order": 2, "issue_type": "Tie-In",
         "issue": {"id": 12, "number": "1", "cover_date": "2024-02-01", "cv_id": 902,
                   "series": {"id": 790, "name": "Absolute Flash"}}},
        {"order": 3, "issue_type": "Core Issue",
         "issue": {"id": 13, "number": "2", "cover_date": "2024-03-01", "cv_id": 903,
                   "series": {"id": 789, "name": "Absolute Batman"}}},
    ]}
    # call order: detail, items, series 789 detail, series 790 detail
    metron_get([detail, items, _series_detail(789, "Absolute Batman"), _series_detail(790, "Absolute Flash")])

    r = client.post("/api/reading-lists", json={"metron_id": 42, "issue_types": ["Core Issue"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total"] == 3

    # both series created
    names = {s.series_name for s in db.query(Series).all()}
    assert {"Absolute Batman", "Absolute Flash"} <= names
    batman = db.query(Series).filter(Series.metron_series_id == 789).one()

    # only Core issues of Batman are monitored; the Tie-In (Flash #1) is NOT
    mon = {(m.series_id, m.issue_number) for m in db.query(MonitoredIssue).all()}
    assert (batman.id, "1") in mon and (batman.id, "2") in mon
    flash = db.query(Series).filter(Series.metron_series_id == 790).one()
    assert not any(sid == flash.id for sid, _ in mon)


def test_add_monitors_all_when_no_types(client, db, metron_get):
    detail = {"name": "All List"}
    items = {"next": None, "results": [
        {"order": 1, "issue_type": "Tie-In",
         "issue": {"id": 21, "number": "1", "cover_date": "2024-01-01",
                   "series": {"id": 555, "name": "Some Series"}}},
    ]}
    metron_get([detail, items, _series_detail(555, "Some Series")])
    r = client.post("/api/reading-lists", json={"metron_id": 7, "issue_types": None})
    assert r.status_code == 201
    assert db.query(MonitoredIssue).count() == 1  # tie-in monitored because no filter


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
