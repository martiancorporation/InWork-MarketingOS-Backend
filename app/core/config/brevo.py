"""Brevo (transactional email) settings — the daily report email provider.

All optional/None in local dev; the app degrades gracefully (skips sending,
logs why) when unconfigured, same stance as the other optional providers.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class BrevoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="BREVO_",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: str | None = None  # BREVO_API_KEY
    sender_email: str | None = None  # BREVO_SENDER_EMAIL
    sender_name: str = "InWork MarketingOS"  # BREVO_SENDER_NAME

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.sender_email)
