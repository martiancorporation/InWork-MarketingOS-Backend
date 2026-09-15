"""Scheduler cadence settings (reads SCHEDULER_* vars).

Controls how often the standalone scheduler process (``python -m app.scheduler``)
fires each platform-wide sweep — the KPI watchdog (the "refresh from recent data"
loop), the integration sync, and the daily digest build. All are configurable so
the cadence can be tuned per environment without a code change.
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

    # How many clients each sweep processes concurrently. Bounds both the wall
    # time of a sweep (so it doesn't drain serially over thousands of clients)
    # and the load placed on the DB connection pool / external ad-platform
    # APIs at once.
    sweep_concurrency: int = 10  # SCHEDULER_SWEEP_CONCURRENCY

    # KPI watchdog — re-evaluate alerts from the latest data on a 30–60 min loop.
    watchdog_interval_minutes: int = 60  # SCHEDULER_WATCHDOG_INTERVAL_MINUTES

    # Pull fresh insights from connected ad-platform integrations.
    integration_sync_interval_minutes: int = 360  # SCHEDULER_INTEGRATION_SYNC_INTERVAL_MINUTES

    # Build the per-client daily digest (open alerts + onboarding/integration status).
    digest_interval_minutes: int = 1440  # SCHEDULER_DIGEST_INTERVAL_MINUTES
    digest_enabled: bool = True  # SCHEDULER_DIGEST_ENABLED

    # Delete expired `user_sessions` rows (nothing else ever cleans them up).
    session_purge_interval_minutes: int = 60  # SCHEDULER_SESSION_PURGE_INTERVAL_MINUTES

    # Delete `audit_log` rows older than this many days — it's append-only
    # (one row per API request) with no other cleanup path, so left unswept
    # it grows forever.
    audit_log_retention_days: int = 365  # SCHEDULER_AUDIT_LOG_RETENTION_DAYS
    audit_log_purge_interval_minutes: int = 1440  # SCHEDULER_AUDIT_LOG_PURGE_INTERVAL_MINUTES

    # Daily report email — checks every client's local time against 23:30 and
    # sends via Brevo once per client per day. Runs on a short interval (not a
    # true cron) since this scheduler is tick-based; see app/services/report_email/timing.py.
    report_email_enabled: bool = True  # SCHEDULER_REPORT_EMAIL_ENABLED
    report_email_check_interval_minutes: int = 10  # SCHEDULER_REPORT_EMAIL_CHECK_INTERVAL_MINUTES

    # Notification email digest — sends each opted-in user one email covering
    # their new warning/critical notifications since the last sweep.
    notification_email_enabled: bool = True  # SCHEDULER_NOTIFICATION_EMAIL_ENABLED
    notification_email_interval_minutes: int = 15  # SCHEDULER_NOTIFICATION_EMAIL_INTERVAL_MINUTES

    # Automatic month-ahead content plan generation — once a client's local
    # day reaches the 15th, auto-draft next month's plan so management has
    # time to review before it starts. Checked frequently (like the report-
    # email job); the real "once a month" gate is due-day + a dedupe log.
    auto_plan_generation_enabled: bool = True  # SCHEDULER_AUTO_PLAN_GENERATION_ENABLED
    auto_plan_generation_check_interval_minutes: int = (
        60  # SCHEDULER_AUTO_PLAN_GENERATION_CHECK_INTERVAL_MINUTES
    )
