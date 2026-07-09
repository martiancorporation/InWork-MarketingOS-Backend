"""Thin async wrapper around the Anthropic (Claude) Messages API.

Reads the API key + model from settings (never hardcoded). The ``anthropic``
SDK is imported lazily so the app runs without it installed / without a key —
callers check ``is_configured`` and fall back gracefully.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.exceptions import ServiceUnavailableError


class AnthropicClient:
    def __init__(self) -> None:
        self._settings = get_settings().ai

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    async def complete(
        self, *, system: str, prompt: str, max_tokens: int | None = None
    ) -> str:
        if not self.is_configured:
            raise ServiceUnavailableError("AI provider is not configured.")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise ServiceUnavailableError("Anthropic SDK is not installed.") from exc

        client = AsyncAnthropic(api_key=self._settings.api_key)
        message = await client.messages.create(
            model=self._settings.model,
            max_tokens=max_tokens or self._settings.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
