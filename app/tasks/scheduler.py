"""Scheduled background jobs — the registry the scheduler process runs.

Each job is a platform-wide sweep implemented in ``SchedulerService`` and driven
on a fixed interval by ``app/scheduler.py``. Jobs run on their own short-lived
session and isolate per-client failures, so a scheduled run is safe to repeat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.config.scheduler import SchedulerSettings
from app.db.session import get_session_factory
from app.services.scheduler_service import SchedulerService

logger = logging.getLogger("app.scheduler")

WATCHDOG_JOB = "kpi_watchdog"
INTEGRATION_SYNC_JOB = "integration_sync"
DIGEST_JOB = "daily_digest"


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval_seconds: int
    description: str


def build_jobs(settings: SchedulerSettings | None = None) -> list[ScheduledJob]:
    """Assemble the scheduled-job registry from the configured cadence."""
    s = settings or get_settings().scheduler
    jobs = [
        ScheduledJob(
            WATCHDOG_JOB,
            s.watchdog_interval_minutes * 60,
            "Evaluate KPI alerts for all active clients",
        ),
        ScheduledJob(
            INTEGRATION_SYNC_JOB,
            s.integration_sync_interval_minutes * 60,
            "Sync connected ad-platform integrations",
        ),
    ]
    if s.digest_enabled:
        jobs.append(
            ScheduledJob(
                DIGEST_JOB,
                s.digest_interval_minutes * 60,
                "Build the per-client daily digest",
            )
        )
    return jobs


# Default registry (configured cadence). The scheduler process rebuilds this.
JOBS: list[ScheduledJob] = build_jobs()


async def run_job(name: str) -> None:
    """Run one scheduled job by name on a fresh session."""
    session = get_session_factory()()
    try:
        service = SchedulerService(session)
        if name == WATCHDOG_JOB:
            result = service.run_watchdog_sweep()
            logger.info(
                "watchdog sweep: clients=%d opened=%d updated=%d resolved=%d",
                result.clients, result.opened, result.updated, result.auto_resolved,
            )
        elif name == INTEGRATION_SYNC_JOB:
            sync = await service.sync_integrations_sweep()
            logger.info(
                "integration sync sweep: clients=%d synced=%d failed=%d",
                sync.clients, sync.synced, sync.failed,
            )
        elif name == DIGEST_JOB:
            digests = service.build_all_digests()
            logger.info("daily digest: built %d client digest(s)", digests.total)
        else:
            logger.warning("Unknown scheduled job: %s", name)
    finally:
        session.close()
