"""run_scraper must log a Cloudflare-gate 403 as a quiet WARNING (no traceback)
but keep genuinely-unexpected failures loud (ERROR + exc_info), and continue to
the next series in either case. All collaborators mocked — no DB/network."""
import logging

import requests

import main


def _series(pub, name):
    # run_scraper unpacks (series_id, entry, monitored_regular, monitored_annual);
    # entry[0]=publisher, entry[1]=series_name.
    return (1, (pub, name, "2024", None, None, None, None), [], [])


def _http_403():
    err = requests.exceptions.HTTPError("403 Forbidden")
    err.response = type("R", (), {"status_code": 403})()
    return err


def test_403_is_quiet_warning_others_loud_and_loop_continues(monkeypatch, caplog):
    seen = []

    monkeypatch.setattr(main, "_load_series", lambda: [
        _series("Boom! Studios", "Book of Butcher"),   # → 403, quiet
        _series("Boom! Studios", "Book of Cutter"),    # → generic, loud
    ])
    monkeypatch.setattr(main, "search_comics", lambda entry: ["x"])
    monkeypatch.setattr(main, "create_series_directory", lambda entry: "/tmp")

    def _fail(entry, *a, **k):
        seen.append(entry[1])
        raise _http_403() if entry[1] == "Book of Butcher" else RuntimeError("boom")
    monkeypatch.setattr(main, "check_and_download_comics", _fail)

    with caplog.at_level(logging.WARNING):
        main.run_scraper()

    # Both series attempted despite the first failing.
    assert seen == ["Book of Butcher", "Book of Cutter"]

    butcher = [r for r in caplog.records if "Book of Butcher" in r.getMessage()]
    cutter = [r for r in caplog.records if "Book of Cutter" in r.getMessage()]
    assert len(butcher) == 1 and len(cutter) == 1

    # 403 → WARNING, no traceback, names Cloudflare.
    assert butcher[0].levelno == logging.WARNING
    assert butcher[0].exc_info is None
    assert "Cloudflare" in butcher[0].getMessage()

    # generic → ERROR with traceback attached.
    assert cutter[0].levelno == logging.ERROR
    assert cutter[0].exc_info is not None


def test_is_http_403_helper():
    assert main._is_http_403(_http_403()) is True
    err404 = requests.exceptions.HTTPError("404")
    err404.response = type("R", (), {"status_code": 404})()
    assert main._is_http_403(err404) is False
    assert main._is_http_403(RuntimeError("no response attr")) is False
