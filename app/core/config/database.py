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

    # Connection-pool tuning. Sizing rule: per-process, so total server-side
    # connections ≈ (pool_size + max_overflow) × uvicorn workers — keep that
    # under the database/pooler ceiling (e.g. Neon). recycle avoids handing out
    # connections the server has already dropped.
    pool_size: int = 5  # DATABASE_POOL_SIZE
    max_overflow: int = 10  # DATABASE_MAX_OVERFLOW
    pool_timeout: int = 30  # DATABASE_POOL_TIMEOUT — seconds to wait for a conn
    pool_recycle: int = 1800  # DATABASE_POOL_RECYCLE — seconds; recycle idle conns

    @field_validator("url", mode="before")
    @classmethod
    def convert_postgresql_scheme(cls, v: Any) -> Any:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v
