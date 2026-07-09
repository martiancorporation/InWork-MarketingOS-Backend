"""Database connection settings (reads DATABASE_* env vars)."""

from __future__ import annotations

from typing import Any
from pydantic import field_validator
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

    @field_validator("url", mode="before")
    @classmethod
    def convert_postgresql_scheme(cls, v: Any) -> Any:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v
