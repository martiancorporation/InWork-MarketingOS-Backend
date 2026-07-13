"""Composed application settings.

Each concern is its own small ``BaseSettings`` (app, database, security, ai,
integrations); ``Settings`` groups them so callers use one object:
``get_settings().security.secret_key``. This is the ONLY place environment
values enter the app — nothing else reads ``os.environ``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from app.core.config.ai import AISettings
from app.core.config.app_settings import AppSettings
from app.core.config.database import DatabaseSettings
from app.core.config.integrations import IntegrationsSettings
from app.core.config.security import SecuritySettings
from app.core.config.storage import StorageSettings


class Settings(BaseSettings):
    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ai: AISettings = Field(default_factory=AISettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @model_validator(mode="after")
    def _forbid_placeholder_secret_in_prod(self) -> "Settings":
        if self.app.is_production and self.security.uses_placeholder_secret:
            raise ValueError(
                "SECRET_KEY must be set to a strong value when APP_ENV=production."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""
    return Settings()
