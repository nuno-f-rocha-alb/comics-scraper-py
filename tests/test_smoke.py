"""Import + basic wiring. The app importing at all is itself a real check —
it's exactly what static analysis couldn't fully prove during the migration."""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_overview_empty(client):
    r = client.get("/api/series/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["series"] == []
    assert body["stats"]["series"] == 0


def test_unmatched_api_is_404_not_spa(client):
    """The root catch-all must not swallow unknown /api paths into the SPA shell.
    (Only meaningful when frontend/dist exists so the catch-all is registered;
    otherwise FastAPI 404s anyway — either way it must be 404, never 200 HTML.)"""
    r = client.get("/api/this-does-not-exist")
    assert r.status_code == 404
