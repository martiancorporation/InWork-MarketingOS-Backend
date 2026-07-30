"""Durable job queue for the intelligence pipeline.

``enqueue`` is called inside the request transaction (transactional outbox) so a
committed onboarding change always has its build job — no lost work. Rapid
autosaves are **coalesced**: an existing queued job for the client is reused and
its changed-source set merged, so a burst of edits becomes one build.

``claim_next`` is used by the worker with ``FOR UPDATE SKIP LOCKED`` (Postgres)
to pull work; it never hands out a second job for a client that already has one
running, avoiding profile-version races.

A job whose worker dies mid-run (crash, restart, deploy) is never flipped out of
``running`` by anything else — ``claim_next`` reclaims it (see
``_STALE_RUNNING_TIMEOUT_SECONDS``) before looking for new work, so a dead
worker can't permanently wedge a client's whole queue.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import IntelJobStatus, IntelJobType
from app.models.intel_job import IntelJob
from app.repositories.intel_job_repository import IntelJobRepository

# A real build (document extraction + chunking + embedding + summary/directive
# generation) normally finishes in well under this; past it, the worker that
# claimed the job is presumed dead (crashed, killed, redeployed).
_STALE_RUNNING_TIMEOUT_SECONDS = 1800  # 30 min


class JobQueue:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.jobs = IntelJobRepository(db)

    def reclaim_stale(self) -> int:
        """Requeue (or dead-letter) jobs whose claiming worker never finished.

        The attempt was already counted at claim time (``claim_next`` increments
        ``attempts`` before handing the job out), so this mirrors ``fail()``'s
        backoff/dead-letter rule without incrementing again. Returns how many
        were reclaimed.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=_STALE_RUNNING_TIMEOUT_SECONDS)
        stale = self.db.scalars(
            select(IntelJob).where(
                IntelJob.status == IntelJobStatus.running.value,
                IntelJob.locked_at.is_not(None),
                IntelJob.locked_at < cutoff,
            )
        ).all()
        for job in stale:
            job.status = (
                IntelJobStatus.dead.value
                if job.attempts >= job.max_attempts
                else IntelJobStatus.queued.value
            )
            if job.status == IntelJobStatus.queued.value:
                job.run_after = datetime.now(UTC)
            job.last_error = "Reclaimed: worker did not finish within the stale-job timeout."
            job.locked_by = None
            job.locked_at = None
        if stale:
            self.db.commit()
        return len(stale)

    def enqueue(
        self,
        client_id: uuid.UUID,
        job_type: str = IntelJobType.incremental.value,
        *,
        changed_keys: list[str] | None = None,
        debounce_seconds: int = 0,
    ) -> IntelJob | None:
        """Enqueue (or coalesce into) a build job. No commit — caller owns the txn."""
        if not get_settings().intelligence.enabled:
            return None

        run_after = (
            datetime.now(UTC) + timedelta(seconds=debounce_seconds) if debounce_seconds else None
        )
        existing = self.jobs.pending_for_client(client_id)
        if existing is not None:
            # Coalesce: full_build dominates; union the changed-source sets.
            if job_type == IntelJobType.full_build.value:
                existing.job_type = IntelJobType.full_build.value
            merged = set((existing.payload or {}).get("changed_keys") or [])
            merged.update(changed_keys or [])
            existing.payload = {"changed_keys": sorted(merged)} if merged else existing.payload
            if run_after is not None:
                existing.run_after = run_after
            return existing

        job = IntelJob(
            client_id=client_id,
            job_type=job_type,
            status=IntelJobStatus.queued.value,
            payload={"changed_keys": sorted(set(changed_keys))} if changed_keys else None,
            run_after=run_after,
        )
        return self.jobs.add(job)

    # ---- worker side ----

    def claim_next(self, worker_id: str) -> IntelJob | None:
        """Claim the next runnable job. Commits the claim before returning."""
        self.reclaim_stale()
        now = datetime.now(UTC)
        is_postgres = self.db.bind is not None and self.db.bind.dialect.name == "postgresql"
        stmt = (
            select(IntelJob)
            .where(
                IntelJob.status == IntelJobStatus.queued.value,
                (IntelJob.run_after.is_(None)) | (IntelJob.run_after <= now),
            )
            .order_by(IntelJob.priority.desc(), IntelJob.created_at)
            .limit(1)
        )
        if is_postgres:
            stmt = stmt.with_for_update(skip_locked=True)

        job = self.db.scalar(stmt)
        if job is None:
            return None
        # Serialize per client. Two workers can each claim a *different* queued
        # job for the same client and both pass an unlocked "is anything
        # running?" check (TOCTOU) — so take a transaction-scoped advisory lock
        # keyed by client id first. The lock auto-releases at commit/rollback.
        if is_postgres:
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": str(job.client_id)},
            )
        running = self.db.scalar(
            select(IntelJob).where(
                IntelJob.client_id == job.client_id,
                IntelJob.status == IntelJobStatus.running.value,
            )
        )
        if running is not None:
            return None
        job.status = IntelJobStatus.running.value
        job.locked_by = worker_id
        job.locked_at = now
        job.attempts += 1
        self.db.commit()
        return job

    def succeed(self, job: IntelJob) -> None:
        job.status = IntelJobStatus.succeeded.value
        job.last_error = None
        self.db.commit()

    def fail(self, job: IntelJob, error: str) -> None:
        job.last_error = error[:1000]
        if job.attempts >= job.max_attempts:
            job.status = IntelJobStatus.dead.value
        else:
            job.status = IntelJobStatus.queued.value
            job.run_after = datetime.now(UTC) + timedelta(seconds=min(300, 10 * (2**job.attempts)))
        job.locked_by = None
        job.locked_at = None
        self.db.commit()
