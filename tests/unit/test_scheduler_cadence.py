"""Scheduler cadence is config-driven (SCHEDULER_* → job intervals)."""

from __future__ import annotations

from app.core.config.scheduler import SchedulerSettings
from app.tasks.scheduler import (
    DIGEST_JOB,
    INTEGRATION_SYNC_JOB,
    WATCHDOG_JOB,
    build_jobs,
)


def test_defaults():
    jobs = {j.name: j for j in build_jobs(SchedulerSettings())}
    assert jobs[WATCHDOG_JOB].interval_seconds == 60 * 60  # 60 min
    assert jobs[INTEGRATION_SYNC_JOB].interval_seconds == 360 * 60
    assert jobs[DIGEST_JOB].interval_seconds == 1440 * 60


def test_custom_intervals_are_honored():
    s = SchedulerSettings(
        watchdog_interval_minutes=30,
        integration_sync_interval_minutes=120,
        digest_interval_minutes=720,
    )
    jobs = {j.name: j for j in build_jobs(s)}
    assert jobs[WATCHDOG_JOB].interval_seconds == 30 * 60  # 30-min loop
    assert jobs[INTEGRATION_SYNC_JOB].interval_seconds == 120 * 60
    assert jobs[DIGEST_JOB].interval_seconds == 720 * 60


def test_digest_can_be_disabled():
    jobs = {j.name for j in build_jobs(SchedulerSettings(digest_enabled=False))}
    assert DIGEST_JOB not in jobs
    assert WATCHDOG_JOB in jobs
