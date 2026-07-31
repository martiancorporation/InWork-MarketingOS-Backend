"""Durable job queue for the intelligence pipeline.

``enqueue`` is called inside the request transaction (transactional outbox) so a
committed onboarding change always has its build job — no lost work. Rapid
autosaves are **coalesced**: an existing queued job for the client is reused and
its changed-source set merged, so a burst of edits becomes one build.

``claim_next`` is used by the worker with ``FOR UPDATE SKIP LOCKED`` (Postgres)
to pull work; it excludes any client that already has a job running directly in
its ``WHERE`` clause, so one client's long-running build never head-of-line
blocks every other client's queue (the worker used to give up entirely — and
sleep a full poll interval — the moment its one candidate belonged to a busy
client, even with other runnable jobs waiting behind it).

A job whose worker dies mid-run (crash, restart, deploy) is never flipped out of
``running`` by anything else — ``claim_next`` reclaims it (see
``_STALE_RUNNING_TIMEOUT_SECONDS``) before looking for new work, so a dead
worker can't permanently wedge a client's whole queue. That scan is throttled
(``_RECLAIM_INTERVAL_SECONDS``) rather than run on every poll, since polls fire
every couple of seconds per worker but a stale job is only even possible after
the multi-minute timeout.
"""

from __future__ import annotations

import time
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
# How often claim_next's stale-job sweep actually queries, at most — a stale job
# can't even exist until _STALE_RUNNING_TIMEOUT_SECONDS has passed, so scanning
# on every ~2s poll is pure waste.
_RECLAIM_INTERVAL_SECONDS = 60
# Bounded retries when claim_next loses a same-tick race for a client's advisory
# lock to another worker (see the loop in claim_next) — small since this only
# ever fires under genuine concurrent contention, not in the normal case.
_MAX_CLAIM_ATTEMPTS = 5

# Process-wide (per worker process); the worker loop is a single coroutine, so
# no concurrent access to guard against.
_last_reclaim_at_monotonic: float = 0.0


class JobQueue:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.jobs = IntelJobRepository(db)

    def reclaim_stale(self, *, force: bool = False) -> int:
        """Requeue (or dead-letter) jobs whose claiming worker never finished.

        The attempt was already counted at claim time (``claim_next`` increments
        ``attempts`` before handing the job out), so this mirrors ``fail()``'s
        backoff/dead-letter rule without incrementing again. Returns how many
        were reclaimed. Throttled to ``_RECLAIM_INTERVAL_SECONDS`` unless
        ``force`` is set (tests force it to observe the effect immediately).
        """
        global _last_reclaim_at_monotonic
        now_monotonic = time.monotonic()
        if not force and (now_monotonic - _last_reclaim_at_monotonic) < _RECLAIM_INTERVAL_SECONDS:
            return 0
        _last_reclaim_at_monotonic = now_monotonic

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
        """Claim the next runnable job. Commits the claim before returning.

        Clients with a job already ``running`` are excluded directly in the
        candidate query, so a long build for one client never starves every
        other client's queue — the worker only ever waits on the *specific*
        client whose job it just claimed, not on whichever job happened to
        sort first overall.
        """
        self.reclaim_stale()
        now = datetime.now(UTC)
        is_postgres = self.db.bind is not None and self.db.bind.dialect.name == "postgresql"

        excluded_clients: set[uuid.UUID] = set()
        for _ in range(_MAX_CLAIM_ATTEMPTS):
            running_client_ids = select(IntelJob.client_id).where(
                IntelJob.status == IntelJobStatus.running.value
            )
            stmt = select(IntelJob).where(
                IntelJob.status == IntelJobStatus.queued.value,
                (IntelJob.run_after.is_(None)) | (IntelJob.run_after <= now),
                IntelJob.client_id.not_in(running_client_ids),
            )
            if excluded_clients:
                stmt = stmt.where(IntelJob.client_id.not_in(excluded_clients))
            stmt = stmt.order_by(IntelJob.priority.desc(), IntelJob.created_at).limit(1)
            if is_postgres:
                stmt = stmt.with_for_update(skip_locked=True)

            job = self.db.scalar(stmt)
            if job is None:
                return None

            # Serialize per client. Two workers can each claim a *different*
            # queued job for the same client and both pass the unlocked
            # "anything running?" check above (TOCTOU) — so take a
            # transaction-scoped advisory lock keyed by client id before the
            # authoritative recheck. The lock auto-releases at commit/rollback.
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
                # Lost a same-tick race for this client — try the next
                # candidate instead of giving up the whole poll cycle.
                excluded_clients.add(job.client_id)
                continue

            job.status = IntelJobStatus.running.value
            job.locked_by = worker_id
            job.locked_at = now
            job.attempts += 1
            self.db.commit()
            return job

        return None

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
