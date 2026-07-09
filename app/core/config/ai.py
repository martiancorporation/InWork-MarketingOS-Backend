"""AI provider settings (Anthropic / Claude). Reads ANTHROPIC_* env vars."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ANTHROPIC_",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: str | None = None  # ANTHROPIC_API_KEY — absent in local dev
    model: str = "claude-opus-4-8"  # ANTHROPIC_MODEL
    max_tokens: int = 1024  # ANTHROPIC_MAX_TOKENS

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)
