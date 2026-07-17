"""Scheduler cadence settings (reads SCHEDULER_* vars).

Controls how often the standalone scheduler process (``python -m app.scheduler``)
fires each platform-wide sweep — the KPI watchdog (the "refresh from recent data"
loop RD asked for), the integration sync, and the daily digest build. All are
configurable so the cadence can be tuned per environment without a code change.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="SCHEDULER_",
        extra="ignore",
        case_sensitive=False,
    )

    # How often the loop wakes to check whether any job is due.
    tick_seconds: int = 30  # SCHEDULER_TICK_SECONDS

    # KPI watchdog — re-evaluate alerts from the latest data. RD's 30–60 min loop.
    watchdog_interval_minutes: int = 60  # SCHEDULER_WATCHDOG_INTERVAL_MINUTES

    # Pull fresh insights from connected ad-platform integrations.
    integration_sync_interval_minutes: int = 360  # SCHEDULER_INTEGRATION_SYNC_INTERVAL_MINUTES

    # Build the per-client daily digest (open alerts + onboarding/integration status).
    digest_interval_minutes: int = 1440  # SCHEDULER_DIGEST_INTERVAL_MINUTES
    digest_enabled: bool = True  # SCHEDULER_DIGEST_ENABLED
