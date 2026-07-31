"""``JobQueue.claim_next`` must not let one client's running build starve every
other client's queue (head-of-line blocking) — see app/services/intelligence/job_queue.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.enums import IntelJobStatus
from app.models.intel_job import IntelJob
from app.services.intelligence.job_queue import JobQueue


def _client(db: Session, slug: str) -> Client:
    c = Client(slug=slug, name=slug)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_a_running_clients_queued_job_does_not_block_another_clients_job(
    db_session: Session,
) -> None:
    """Reproduces the starvation bug: client A has a job running AND a second,
    coalesced job queued (sorts first by creation time); client B has its own
    queued job created after. The naive "grab the oldest queued row, bail if its
    client is busy" approach returns None here even though B's job is fully
    claimable — starving B behind A for as long as A's build runs."""
    client_a = _client(db_session, "client-a")
    client_b = _client(db_session, "client-b")
    now = datetime.now(UTC)

    running_a = IntelJob(
        client_id=client_a.id,
        status=IntelJobStatus.running.value,
        locked_by="worker-1",
        locked_at=now,
    )
    db_session.add(running_a)
    db_session.commit()

    queued_a = IntelJob(
        client_id=client_a.id,
        status=IntelJobStatus.queued.value,
        created_at=now - timedelta(seconds=10),  # sorts before B's job
    )
    queued_b = IntelJob(
        client_id=client_b.id,
        status=IntelJobStatus.queued.value,
        created_at=now - timedelta(seconds=5),
    )
    db_session.add_all([queued_a, queued_b])
    db_session.commit()

    claimed = JobQueue(db_session).claim_next("worker-2")

    assert claimed is not None, "client B's runnable job must not starve behind client A's"
    assert claimed.client_id == client_b.id
    assert claimed.status == IntelJobStatus.running.value


def test_returns_none_when_every_queued_job_belongs_to_a_running_client(
    db_session: Session,
) -> None:
    client = _client(db_session, "only-client")
    now = datetime.now(UTC)
    db_session.add(
        IntelJob(
            client_id=client.id,
            status=IntelJobStatus.running.value,
            locked_by="worker-1",
            locked_at=now,
        )
    )
    db_session.add(IntelJob(client_id=client.id, status=IntelJobStatus.queued.value))
    db_session.commit()

    assert JobQueue(db_session).claim_next("worker-2") is None
