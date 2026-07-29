"""Application/server settings (name, environment, API prefix)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "InWork MarketingOS API"
    app_env: str = "local"  # local | development | production
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000
    # When on, every API request is recorded to the audit_log table by the
    # AuditMiddleware. Disabled in the hermetic test suite (see conftest).
    audit_enabled: bool = True
    # When on, every AI provider call is recorded to ai_usage_events (tokens +
    # cost). Disabled in the hermetic test suite (recorder uses a real session).
    ai_usage_enabled: bool = True
    # In-process rate limiting on sensitive routes (login, paid-AI). Disabled in
    # the test suite so repeated logins don't trip it. NOTE: limits are
    # per-process — with multiple workers, use a shared store (Redis) for exact
    # global limits; this is a per-worker first line of defense.
    rate_limit_enabled: bool = True
    # Absolute base URL (scheme + host + api prefix) this backend is publicly
    # reachable at, e.g. "https://backendai.startwithmartian.com/api/v1". Used to
    # build permanent, signed upload-download links (see app/utils/download_link.py)
    # that stay stable regardless of proxy/Host-header quirks. Not derived from
    # `request.base_url` on purpose. None only in local/dev where such links aren't
    # exercised end-to-end.
    public_api_base_url: str | None = None  # PUBLIC_API_BASE_URL

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"
