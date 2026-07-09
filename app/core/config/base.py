"""Application settings, loaded from the environment / a local ``.env`` file.

This is the ONLY place environment values enter the app. No secret values are
hardcoded here — they come from environment variables (see ``.env.example``).

Only the settings needed by the database layer are defined for now; the split
files in this folder (``app_settings``, ``security``, ``ai``, ``integrations``)
are wired in as those features are implemented.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application ----
    app_name: str = "InWork MarketingOS API"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ---- Database ----
    # Overridden by the DATABASE_URL environment variable in every real deployment.
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/inwork"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read once per process)."""
    return Settings()
