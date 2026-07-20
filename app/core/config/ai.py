"""AI provider settings (Anthropic / Claude). Reads ANTHROPIC_* env vars."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="ANTHROPIC_",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: str | None = None  # ANTHROPIC_API_KEY — absent in local dev
    model: str = "claude-opus-4-8"  # ANTHROPIC_MODEL
    max_tokens: int = 1024  # ANTHROPIC_MAX_TOKENS
    # Per-request timeout (seconds) and SDK-level retries (429/5xx, exp backoff).
    timeout_seconds: float = 30.0  # ANTHROPIC_TIMEOUT_SECONDS
    max_retries: int = 2  # ANTHROPIC_MAX_RETRIES

    # Cost-optimization model tiers — the models the cost heuristics
    # (app/ai/cost_optimization.py) route between. Kept in config so model
    # identities are never hard-coded in the heuristic module.
    cheap_model: str = "claude-haiku-4-5-20251001"  # ANTHROPIC_CHEAP_MODEL — data-gathering steps
    mid_model: str = "claude-sonnet-5"  # ANTHROPIC_MID_MODEL — non-gathering steps on the expensive tier
    expensive_models: str = "claude-opus-4-8"  # ANTHROPIC_EXPENSIVE_MODELS — comma-separated tier to route down from

    # Optional pricing override (JSON, USD per 1M tokens). Read through config so
    # nothing outside app/core/config touches the environment. Env var name is
    # kept as AI_PRICING_JSON (not prefixed) for backward compatibility.
    pricing_json: str | None = Field(default=None, validation_alias="AI_PRICING_JSON")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def expensive_model_set(self) -> frozenset[str]:
        """The expensive-tier model ids to route down from (parsed, de-blanked)."""
        return frozenset(m.strip() for m in self.expensive_models.split(",") if m.strip())
