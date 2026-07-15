"""Authentication & security settings (JWT, CORS).

The ``secret_key`` default is an obviously-insecure development placeholder;
production is *required* to override it via the ``SECRET_KEY`` env var — this is
enforced in ``Settings`` (see base.py).
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES

DEV_SECRET_PLACEHOLDER = "dev-insecure-secret-change-me-in-production-0123456789"
# HS256 signing key entropy floor. 32 chars is the minimum defensible length;
# generate with:  python -c "import secrets; print(secrets.token_urlsafe(48))"
MIN_SECRET_LENGTH = 32


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    secret_key: str = DEV_SECRET_PLACEHOLDER  # SECRET_KEY
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    # Comma-separated list in the env; exposed as a parsed list below.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @field_validator("secret_key")
    @classmethod
    def _reject_weak_secret(cls, value: str) -> str:
        """A too-short signing key is trivially brute-forceable — reject it in
        every environment (the placeholder itself is comfortably long)."""
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters."
            )
        return value

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_with_credentials(cls, value: str) -> str:
        """``allow_origins=['*']`` with ``allow_credentials=True`` is rejected by
        browsers and is a security foot-gun — forbid the wildcard outright."""
        if "*" in value:
            raise ValueError(
                "CORS_ORIGINS must list explicit origins; '*' is not allowed "
                "with credentialed requests."
            )
        return value

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uses_placeholder_secret(self) -> bool:
        return self.secret_key == DEV_SECRET_PLACEHOLDER
