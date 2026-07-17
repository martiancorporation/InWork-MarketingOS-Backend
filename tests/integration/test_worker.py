"""Regression tests for the intelligence worker's claim → process flow.

Guards the bug where a job claimed (and committed) in one session was then
dereferenced in another — ``process_job`` used the detached instance and crashed
with ``DetachedInstanceError``, so the RAG profile build never completed. The fix
passes the job *id* and re-loads it in the processing session; these tests
reproduce the exact cross-session scenario.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as SASession

from app import worker
from app.integrations.embeddings.fake import FakeEmbedder
from app.models.intel_job import IntelJob
from app.services.intelligence.job_queue import JobQueue
from tests.conftest import API
from tests.helpers import onboarding_payload


def _onboard(client: TestClient, admin_headers) -> str:
    resp = client.post(f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload())
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def test_claim_in_one_session_process_in_another_completes(
    client: TestClient, admin_headers: dict, db_session: Session
):
    # Onboarding enqueues a build job.
    _onboard(client, admin_headers)

    # Claim it in a SEPARATE session on the same engine, then close that session
    # so the job is detached — exactly what the worker does in production.
    engine = db_session.get_bind()
    claim_session = SASession(bind=engine)
    job = JobQueue(claim_session).claim_next("worker-test")
    assert job is not None, "expected a queued build job from onboarding"
    job_id = job.id  # read while the claim session is open
    claim_session.close()

    # Process by id in a different session — must not raise DetachedInstanceError.
    asyncio.run(
        worker.process_job(job_id, session=db_session, embedder=FakeEmbedder(1024), storage=None)
    )

    db_session.expire_all()
    done = db_session.get(IntelJob, job_id)
    assert done.status == "succeeded", f"job stuck at {done.status} (last_error={done.last_error})"


def test_process_missing_job_is_noop(db_session: Session):
    import uuid

    # A job id that doesn't exist must simply no-op, never crash.
    asyncio.run(
        worker.process_job(uuid.uuid4(), session=db_session, embedder=FakeEmbedder(1024), storage=None)
    )
