"""Durable job queue for the intelligence pipeline.

``enqueue`` is called inside the request transaction (transactional outbox) so a
committed onboarding change always has its build job — no lost work. Rapid
autosaves are **coalesced**: an existing queued job for the client is reused and
its changed-source set merged, so a burst of edits becomes one build.

``claim_next`` is used by the worker with ``FOR UPDATE SKIP LOCKED`` (Postgres)
to pull work; it never hands out a second job for a client that already has one
running, avoiding profile-version races.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import IntelJobStatus, IntelJobType
from app.models.intel_job import IntelJob
from app.repositories.intel_job_repository import IntelJobRepository


class JobQueue:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.jobs = IntelJobRepository(db)

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
            datetime.now(timezone.utc) + timedelta(seconds=debounce_seconds)
            if debounce_seconds
            else None
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
        now = datetime.now(timezone.utc)
        stmt = (
            select(IntelJob)
            .where(
                IntelJob.status == IntelJobStatus.queued.value,
                (IntelJob.run_after.is_(None)) | (IntelJob.run_after <= now),
            )
            .order_by(IntelJob.priority.desc(), IntelJob.created_at)
            .limit(1)
        )
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        job = self.db.scalar(stmt)
        if job is None:
            return None
        # Serialize per client: skip if another job for this client is running.
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
            job.run_after = datetime.now(timezone.utc) + timedelta(
                seconds=min(300, 10 * (2 ** job.attempts))
            )
        job.locked_by = None
        job.locked_at = None
        self.db.commit()
