"""Demo-data settings (reads DEMO_* vars).

Until the ad-platform credentials land there is no real data to report on, so a
newly created client would open onto empty dashboards. When ``seed_on_create`` is
on, creating a client also writes a synthetic history for it — see
``app/services/demo_data_service.py``.

This is scaffolding, **off by default** — a real deployment should not silently
fabricate spend/leads data for real clients. Opt in explicitly
(``DEMO_SEED_ON_CREATE=true``) for a demo/sales environment. Existing synthetic
rows stay identifiable (``analytics_daily.source='synthetic'``) and a connector
sync overwrites them in place regardless of this flag.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class DemoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="DEMO_",
        extra="ignore",
        case_sensitive=False,
    )

    # Seed a synthetic history when a client is created. Off by default — a
    # production deployment must opt in explicitly.
    seed_on_create: bool = False  # DEMO_SEED_ON_CREATE

    # Days of history to generate. 90 gives the dashboard's 7/30/90-day
    # comparisons something to compare against.
    seed_days: int = 90  # DEMO_SEED_DAYS
