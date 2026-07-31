"""Unit tests for ``JobQueue.reclaim_stale`` — a job whose claiming worker died
mid-run must not stay ``running`` forever (and block that client's whole queue).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

import app.services.intelligence.job_queue as job_queue_module
from app.models.client import Client
from app.models.enums import IntelJobStatus
from app.models.intel_job import IntelJob
from app.services.intelligence.job_queue import _STALE_RUNNING_TIMEOUT_SECONDS, JobQueue


@pytest.fixture(autouse=True)
def _reset_reclaim_throttle():
    """reclaim_stale() is throttled process-wide (see _RECLAIM_INTERVAL_SECONDS)
    so it doesn't full-scan on every ~2s worker poll. Reset the module-level
    timer before each test so these tests observe every call, uncoupled from
    real wall-clock timing / test execution order."""
    job_queue_module._last_reclaim_at_monotonic = 0.0
    yield
    job_queue_module._last_reclaim_at_monotonic = 0.0


def _client(db: Session) -> Client:
    c = Client(slug="reclaim-co", name="Reclaim Co")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _running_job(db: Session, client: Client, *, locked_at, attempts: int = 1) -> IntelJob:
    job = IntelJob(
        client_id=client.id,
        status=IntelJobStatus.running.value,
        attempts=attempts,
        max_attempts=3,
        locked_by="worker-dead",
        locked_at=locked_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_reclaim_requeues_a_stale_running_job(db_session: Session) -> None:
    client = _client(db_session)
    stale_at = datetime.now(UTC) - timedelta(seconds=_STALE_RUNNING_TIMEOUT_SECONDS + 60)
    job = _running_job(db_session, client, locked_at=stale_at, attempts=1)

    reclaimed = JobQueue(db_session).reclaim_stale()

    assert reclaimed == 1
    db_session.refresh(job)
    assert job.status == IntelJobStatus.queued.value
    assert job.locked_by is None
    assert job.locked_at is None
    assert job.run_after is not None
    assert "Reclaimed" in job.last_error


def test_reclaim_dead_letters_when_attempts_exhausted(db_session: Session) -> None:
    client = _client(db_session)
    stale_at = datetime.now(UTC) - timedelta(seconds=_STALE_RUNNING_TIMEOUT_SECONDS + 60)
    job = _running_job(db_session, client, locked_at=stale_at, attempts=3)  # == max_attempts

    JobQueue(db_session).reclaim_stale()

    db_session.refresh(job)
    assert job.status == IntelJobStatus.dead.value


def test_reclaim_leaves_a_fresh_running_job_alone(db_session: Session) -> None:
    client = _client(db_session)
    job = _running_job(db_session, client, locked_at=datetime.now(UTC), attempts=1)

    reclaimed = JobQueue(db_session).reclaim_stale()

    assert reclaimed == 0
    db_session.refresh(job)
    assert job.status == IntelJobStatus.running.value


def test_claim_next_reclaims_before_claiming_new_work(db_session: Session) -> None:
    """A dead worker's stale job must not permanently block the same client's
    queue from ever being claimed again."""
    client = _client(db_session)
    stale_at = datetime.now(UTC) - timedelta(seconds=_STALE_RUNNING_TIMEOUT_SECONDS + 60)
    stuck = _running_job(db_session, client, locked_at=stale_at, attempts=1)

    queue = JobQueue(db_session)
    claimed = queue.claim_next("worker-new")

    assert claimed is not None
    assert claimed.id == stuck.id  # the reclaimed job itself is now claimable
    assert claimed.status == IntelJobStatus.running.value
    assert claimed.locked_by == "worker-new"
