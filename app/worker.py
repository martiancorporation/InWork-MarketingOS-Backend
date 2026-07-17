"""Intelligence worker: drains the ``intel_jobs`` queue and builds profiles.

Run as its own process (scales horizontally — each instance claims different
jobs via ``FOR UPDATE SKIP LOCKED``):

    python -m app.worker

Each job runs the full pipeline for one client, retries with backoff on failure,
and is dead-lettered after ``max_attempts``. Storage/embedding backends degrade
gracefully, so the worker keeps producing field-based profiles even if S3 or the
embedding provider is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.integrations.aws import S3Storage
from app.integrations.embeddings import get_embedder
from app.models.client import Client
from app.models.intel_job import IntelJob
from app.services.intelligence.job_queue import JobQueue
from app.services.intelligence.orchestrator import IntelligenceOrchestrator

logger = logging.getLogger("app.worker")


async def process_job(job_id: uuid.UUID, *, session=None, embedder=None, storage=None) -> None:
    """Run one claimed job's pipeline.

    The job was claimed (and committed) in a *separate* session, so a passed-in
    instance would be detached with expired attributes (``DetachedInstanceError``).
    We take the job *id* and re-load it fresh in the processing session.

    Uses its own session/embedder/storage in production; all three are injectable
    for tests (so the build runs against the test DB with a fake embedder).
    """
    from app.core.config import get_settings

    own = session is None
    session = session or get_session_factory()()
    if embedder is None:
        embedder = get_embedder()
    if storage is None and own:
        storage = S3Storage(get_settings().storage)
    queue = JobQueue(session)
    job = session.get(IntelJob, job_id)
    if job is None:
        return
    try:
        client = session.get(Client, job.client_id)
        if client is None:
            queue.succeed(job)  # client gone — nothing to build
            return
        orchestrator = IntelligenceOrchestrator(
            session, embedder=embedder, storage=storage
        )
        changed = set((job.payload or {}).get("changed_keys") or [])
        await orchestrator.build(
            client, job_type=job.job_type, changed_keys=changed or None
        )
        queue.succeed(job)
    except Exception as exc:  # noqa: BLE001 - retry/dead-letter, never crash the loop
        logger.exception("Job %s failed", job_id)
        session.rollback()
        queue.fail(job, str(exc))
    finally:
        if own:
            session.close()


async def _claim_and_run(worker_id: str) -> bool:
    session = get_session_factory()()
    try:
        job = JobQueue(session).claim_next(worker_id)
        # Read the id while the session is still open (claim commits → attributes
        # expire); the job is then processed in its own fresh session.
        job_id = job.id if job is not None else None
    finally:
        session.close()
    if job_id is None:
        return False
    await process_job(job_id)
    return True


async def run_worker(*, poll_interval: float = 2.0, worker_id: str | None = None) -> None:
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
    logger.info("Intelligence worker %s started", worker_id)
    while True:
        try:
            did_work = await _claim_and_run(worker_id)
        except Exception:  # noqa: BLE001
            logger.exception("Worker loop error")
            did_work = False
        if not did_work:
            await asyncio.sleep(poll_interval)


def main() -> None:
    configure_logging(debug=False)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
