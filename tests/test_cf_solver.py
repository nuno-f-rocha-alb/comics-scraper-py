"""cf_solver: FlareSolverr client. Network mocked — no real requests.

Proves the enabled/disabled no-op boundary, the /v1 payload (incl. optional proxy),
solution parsing, per-host clearance caching, and quiet failure handling.
"""
import pytest

import downloader.cf_solver as cf


# Realistic FlareSolverr v1 response shape.
def _ok_response(html="<html>ok</html>", ua="Mozilla/5.0 FS"):
    return {
        "status": "ok",
        "solution": {
            "url": "https://getcomics.org/x",
            "status": 200,
            "response": html,
            "cookies": [
                {"name": "cf_clearance", "value": "abc", "domain": "getcomics.org"},
                {"name": "other", "value": "1"},
            ],
            "userAgent": ua,
        },
    }


class FakePost:
    """Records the last POST and returns a canned JSON (or raises)."""
    def __init__(self, json_data=None, status=200, raise_exc=None):
        self.json_data = json_data
        self.status = status
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.raise_exc:
            raise self.raise_exc
        return self

    def raise_for_status(self):
        if self.status != 200:
            raise Exception(f"HTTP {self.status}")

    def json(self):
        return self.json_data


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Every test starts enabled, no proxy, empty clearance cache."""
    cf._clearance.clear()
    monkeypatch.setattr(cf, "FLARESOLVERR_URL", "http://flaresolverr:8191")
    monkeypatch.setattr(cf, "PROXY_URL", "")
    monkeypatch.setattr(cf, "CF_SOLVER_TIMEOUT", 60000)


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(cf, "FLARESOLVERR_URL", "")
    post = FakePost()
    monkeypatch.setattr(cf.requests, "post", post)

    assert cf.get_page("https://getcomics.org/x") is None
    assert cf.clearance_for("https://getcomics.org/x") == (None, None)
    assert post.calls == []  # never hits the network when disabled


def test_solve_posts_v1_payload_and_parses(monkeypatch):
    post = FakePost(json_data=_ok_response())
    monkeypatch.setattr(cf.requests, "post", post)

    sol = cf.solve("https://getcomics.org/x")

    assert post.calls[0]["url"] == "http://flaresolverr:8191/v1"
    body = post.calls[0]["json"]
    assert body["cmd"] == "request.get"
    assert body["url"] == "https://getcomics.org/x"
    assert body["maxTimeout"] == 60000
    assert "proxy" not in body  # unset PROXY_URL → no proxy key
    assert sol["html"] == "<html>ok</html>"
    assert sol["cookies"] == {"cf_clearance": "abc", "other": "1"}
    assert sol["ua"] == "Mozilla/5.0 FS"


def test_proxy_included_only_when_set(monkeypatch):
    monkeypatch.setattr(cf, "PROXY_URL", "http://user:pass@host:3128")
    post = FakePost(json_data=_ok_response())
    monkeypatch.setattr(cf.requests, "post", post)

    cf.solve("https://getcomics.org/x")
    assert post.calls[0]["json"]["proxy"] == {"url": "http://user:pass@host:3128"}


def test_clearance_cached_per_host_no_second_post(monkeypatch):
    post = FakePost(json_data=_ok_response())
    monkeypatch.setattr(cf.requests, "post", post)

    cookies, ua = cf.clearance_for("https://getcomics.org/x")
    assert cookies == {"cf_clearance": "abc", "other": "1"}
    assert ua == "Mozilla/5.0 FS"

    # Second lookup on the same host is served from cache — no new POST.
    cf.clearance_for("https://getcomics.org/y")
    assert len(post.calls) == 1


def test_post_exception_returns_none_quietly(monkeypatch, caplog):
    post = FakePost(raise_exc=RuntimeError("boom"))
    monkeypatch.setattr(cf.requests, "post", post)

    import logging
    with caplog.at_level(logging.WARNING):
        assert cf.solve("https://getcomics.org/x") is None
        assert cf.get_page("https://getcomics.org/x") is None
    assert any("FlareSolverr request failed" in r.message for r in caplog.records)
    # WARNING only — no ERROR/traceback for an expected skip-and-retry.
    assert all(r.levelno <= logging.WARNING for r in caplog.records)


def test_non_ok_status_returns_none(monkeypatch):
    post = FakePost(json_data={"status": "error", "message": "challenge failed"})
    monkeypatch.setattr(cf.requests, "post", post)
    assert cf.solve("https://getcomics.org/x") is None
