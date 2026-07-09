"""Database connection settings (reads DATABASE_* env vars)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="DATABASE_",
        extra="ignore",
        case_sensitive=False,
    )

    # DATABASE_URL — overridden in every real deployment.
    url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/inwork"
    echo: bool = False  # DATABASE_ECHO — log SQL when true
