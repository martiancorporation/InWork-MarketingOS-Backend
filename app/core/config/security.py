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
    access_token_expire_minutes: int = 420  # 7 hours
    # Comma-separated list in the env; exposed as a parsed list below.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    # Dedicated key for encrypting stored OAuth tokens at rest (see
    # app/integrations/crypto.py). Falls back to SECRET_KEY when unset, for
    # backward compatibility — but that couples two very different blast
    # radii: rotating SECRET_KEY (e.g. after a JWT-signing leak) would
    # otherwise also silently destroy every client's stored ad-platform
    # credentials, and a single SECRET_KEY compromise would yield both token
    # forgery *and* decryption of all customer OAuth tokens. Set this
    # explicitly (and separately) in production.
    encryption_key: str | None = None  # ENCRYPTION_KEY

    @field_validator("secret_key")
    @classmethod
    def _reject_weak_secret(cls, value: str) -> str:
        """A too-short signing key is trivially brute-forceable — reject it in
        every environment (the placeholder itself is comfortably long)."""
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(f"SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters.")
        return value

    @field_validator("encryption_key")
    @classmethod
    def _reject_weak_encryption_key(cls, value: str | None) -> str | None:
        if value is not None and len(value) < MIN_SECRET_LENGTH:
            raise ValueError(f"ENCRYPTION_KEY must be at least {MIN_SECRET_LENGTH} characters.")
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

    @property
    def token_encryption_key(self) -> str:
        """The key TokenCipher derives its Fernet key from — ENCRYPTION_KEY if
        set, else SECRET_KEY (backward-compatible default)."""
        return self.encryption_key or self.secret_key
