"""Scheduled background jobs — the registry the scheduler process runs.

Each job is a platform-wide sweep implemented in ``SchedulerService`` and driven
on a fixed interval by ``app/scheduler.py``. Jobs run on their own short-lived
session and isolate per-client failures, so a scheduled run is safe to repeat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db.session import get_session_factory
from app.services.scheduler_service import SchedulerService

logger = logging.getLogger("app.scheduler")


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval_seconds: int
    description: str


WATCHDOG = ScheduledJob("kpi_watchdog", 60 * 60, "Evaluate KPI alerts for all active clients")
INTEGRATION_SYNC = ScheduledJob(
    "integration_sync", 6 * 60 * 60, "Sync connected ad-platform integrations"
)

JOBS: list[ScheduledJob] = [WATCHDOG, INTEGRATION_SYNC]


async def run_job(name: str) -> None:
    """Run one scheduled job by name on a fresh session."""
    session = get_session_factory()()
    try:
        service = SchedulerService(session)
        if name == WATCHDOG.name:
            result = service.run_watchdog_sweep()
            logger.info(
                "watchdog sweep: clients=%d opened=%d updated=%d resolved=%d",
                result.clients, result.opened, result.updated, result.auto_resolved,
            )
        elif name == INTEGRATION_SYNC.name:
            sync = await service.sync_integrations_sweep()
            logger.info(
                "integration sync sweep: clients=%d synced=%d failed=%d",
                sync.clients, sync.synced, sync.failed,
            )
        else:
            logger.warning("Unknown scheduled job: %s", name)
    finally:
        session.close()
