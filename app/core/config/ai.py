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
    # No model id lives here, or anywhere else in settings/env — every AI call
    # resolves its model from the admin-editable ``ai_model_routes`` table
    # (app/ai/model_router.py, app/services/ai_model_route_service.py),
    # itself populated from OpenRouter's live model catalog. A feature whose
    # category has no configured route yet degrades the same way an
    # unconfigured provider does (see app/integrations/llm/openrouter.py).
    base_url: str = "https://openrouter.ai/api/v1"  # OPENROUTER_BASE_URL
    max_tokens: int = 1024  # OPENROUTER_MAX_TOKENS
    # Per-request timeout (seconds) and client-side retries.
    timeout_seconds: float = 30.0  # OPENROUTER_TIMEOUT_SECONDS
    max_retries: int = 2  # OPENROUTER_MAX_RETRIES

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
