"""AI provider settings (OpenRouter). Reads OPENROUTER_* env vars.

OpenRouter proxies many vendors' models behind one OpenAI-compatible API —
this is the ONE settings module that names the actual LLM vendor; the client
that reads it (``app/integrations/llm/openrouter.py``) is reached everywhere
else only through ``get_llm_client()`` (``app/integrations/llm/factory.py``).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="OPENROUTER_",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: str | None = None  # OPENROUTER_API_KEY — absent in local dev
    # Model ids are "vendor/model", e.g. "anthropic/claude-sonnet-5",
    # "openai/gpt-4o", "google/gemini-2.5-pro" — see openrouter.ai/models.
    model: str = "anthropic/claude-opus-4-8"  # OPENROUTER_MODEL
    base_url: str = "https://openrouter.ai/api/v1"  # OPENROUTER_BASE_URL
    max_tokens: int = 1024  # OPENROUTER_MAX_TOKENS
    # Per-request timeout (seconds) and client-side retries.
    timeout_seconds: float = 30.0  # OPENROUTER_TIMEOUT_SECONDS
    max_retries: int = 2  # OPENROUTER_MAX_RETRIES

    # Cost-optimization model tiers — the models the cost heuristics
    # (app/ai/cost_optimization.py) route between. Kept in config so model
    # identities are never hard-coded in the heuristic module.
    cheap_model: str = "anthropic/claude-haiku-4-5-20251001"  # OPENROUTER_CHEAP_MODEL
    mid_model: str = "anthropic/claude-sonnet-5"  # OPENROUTER_MID_MODEL
    expensive_models: str = (
        "anthropic/claude-opus-4-8"  # OPENROUTER_EXPENSIVE_MODELS — comma-separated
    )

    # Optional OpenRouter attribution headers (show up on the OpenRouter
    # dashboard) — never required for the API to work.
    site_url: str | None = None  # OPENROUTER_SITE_URL -> HTTP-Referer
    app_name: str | None = None  # OPENROUTER_APP_NAME -> X-Title

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
