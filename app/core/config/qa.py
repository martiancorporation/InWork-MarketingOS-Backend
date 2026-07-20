"""Second-provider QA + OpenAI-compatible client settings.

Two independent concerns, one module:

* ``OpenAISettings`` (``OPENAI_*``) — credentials/model for the second LLM
  provider used by the cross-provider QA layer. Any OpenAI-compatible endpoint
  works via ``OPENAI_BASE_URL`` (Azure OpenAI, local gateways, …).
* ``QASettings`` (``AI_QA_*``) — the QA feature toggle + which provider does the
  independent review. All optional; QA is off by default and degrades to a clean
  ``not_reviewed`` passthrough when disabled or the provider is unconfigured.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="OPENAI_",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: str | None = None  # OPENAI_API_KEY — absent in local dev
    model: str = "gpt-4o-mini"  # OPENAI_MODEL — a cheap reviewer by default
    # OpenAI-compatible base; override for Azure OpenAI / gateways / local models.
    base_url: str = "https://api.openai.com/v1"  # OPENAI_BASE_URL
    max_tokens: int = 1024  # OPENAI_MAX_TOKENS
    timeout_seconds: float = 30.0  # OPENAI_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


class QASettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="AI_QA_",
        extra="ignore",
        case_sensitive=False,
    )

    # Master switch for the cross-provider QA pass. Off by default: QA only runs
    # when explicitly enabled AND the chosen provider is configured.
    enabled: bool = False  # AI_QA_ENABLED
    # Which provider performs the independent review. Deliberately a DIFFERENT
    # vendor from the generator (Anthropic) so single-vendor bias is countered.
    provider: str = "openai"  # AI_QA_PROVIDER — "openai" | "anthropic"
