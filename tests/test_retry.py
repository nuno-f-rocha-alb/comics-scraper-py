"""Auto-retry on transient download failures: network blips / rate limits
re-enqueue the job (capped + backoff); permanent errors fail immediately."""
import pytest
import requests

import web.worker as worker
from web.models import DownloadJob, Series


@pytest.fixture(autouse=True)
def _clear_attempts():
    """_attempts is module-level — reset it so tests don't leak across cases."""
    worker._attempts.clear()
    yield
    worker._attempts.clear()


def test_is_transient():
    assert worker._is_transient(requests.exceptions.Timeout())
    assert worker._is_transient(requests.exceptions.ConnectionError())
    err = requests.exceptions.HTTPError()
    err.response = type("R", (), {"status_code": 429})()
    assert worker._is_transient(err)
    err404 = requests.exceptions.HTTPError()
    err404.response = type("R", (), {"status_code": 404})()
    assert not worker._is_transient(err404)
    assert not worker._is_transient(Exception("no download link found"))


class _FakeTimer:
    last = None

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args = delay, fn, args
        _FakeTimer.last = self

    def start(self):
        pass


def _make_job(db, status="queued"):
    s = Series(publisher="Image", series_name="Saga", year=2012)
    db.add(s)
    db.flush()
    job = DownloadJob(series_id=s.id, issue_number="1", search_term="Saga", status=status)
    db.add(job)
    db.commit()
    return job.id


def test_transient_failure_reenqueues(db, monkeypatch):
    job_id = _make_job(db)
    monkeypatch.setattr(worker.threading, "Timer", _FakeTimer)
    monkeypatch.setattr(worker, "_download_issue",
                        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    worker._attempts.pop(job_id, None)

    worker._process(job_id)

    db.expire_all()
    job = db.get(DownloadJob, job_id)
    assert job.status == "queued"            # re-queued, not failed
    assert job.finished_at is None           # not terminal
    assert worker._attempts[job_id] == 1
    assert _FakeTimer.last.args == (job_id,)  # scheduled a retry
    worker._attempts.pop(job_id, None)


def test_permanent_failure_fails_immediately(db, monkeypatch):
    job_id = _make_job(db)
    monkeypatch.setattr(worker, "_download_issue",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("No download link found")))

    worker._process(job_id)

    db.expire_all()
    job = db.get(DownloadJob, job_id)
    assert job.status == "failed"
    assert job.finished_at is not None
    assert job_id not in worker._attempts


def test_queued_cancel_clears_retry_state(db, monkeypatch):
    """A job cancelled while queued after a prior transient attempt must end
    fully terminal: no leftover _attempts entry, no stale 'retrying…' error."""
    job_id = _make_job(db)
    # Simulate a prior transient attempt that left bookkeeping behind.
    worker._attempts[job_id] = 1
    db.get(DownloadJob, job_id).error = "retrying (1/3): boom"
    db.commit()
    worker.request_cancel(job_id)

    worker._process(job_id)

    db.expire_all()
    job = db.get(DownloadJob, job_id)
    assert job.status == "cancelled"
    assert job.finished_at is not None
    assert not job.error
    assert job_id not in worker._attempts
    assert not worker._is_cancelled(job_id)


def test_retries_exhausted_fails(db, monkeypatch):
    job_id = _make_job(db)
    monkeypatch.setattr(worker, "_download_issue",
                        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.Timeout()))
    worker._attempts[job_id] = worker.MAX_RETRIES - 1  # one attempt left → exceeds cap

    worker._process(job_id)

    db.expire_all()
    job = db.get(DownloadJob, job_id)
    assert job.status == "failed"
    assert job_id not in worker._attempts
