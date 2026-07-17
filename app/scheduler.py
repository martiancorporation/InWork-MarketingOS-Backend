"""Scheduler process: runs the platform's periodic jobs on a cadence.

Run as its own process alongside the API and the intelligence worker:

    python -m app.scheduler

It drives the sweeps in ``app/tasks/scheduler.py`` (KPI watchdog, integration
sync) at their configured intervals. Each firing runs on a fresh session and
isolates per-client failures, so a crashed run never corrupts state — the next
tick simply picks up again. Keep a single instance running (the jobs are
cross-client sweeps, not per-item work that needs sharding).
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.core.logging import configure_logging
from app.tasks.scheduler import JOBS, run_job

logger = logging.getLogger("app.scheduler")

_TICK_SECONDS = 30


async def main() -> None:
    configure_logging()
    logger.info("Scheduler started with jobs: %s", ", ".join(j.name for j in JOBS))
    # Fire each job on startup, then on its interval.
    last_run: dict[str, float] = {j.name: 0.0 for j in JOBS}
    while True:
        now = time.monotonic()
        for job in JOBS:
            if now - last_run[job.name] >= job.interval_seconds:
                try:
                    await run_job(job.name)
                except Exception:  # a failed run must not kill the scheduler
                    logger.exception("Scheduled job %s failed", job.name)
                last_run[job.name] = time.monotonic()
        await asyncio.sleep(_TICK_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
