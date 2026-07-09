"""Thin async wrapper around the Anthropic (Claude) Messages API.

Three entry points:
- ``complete`` — a plain single-shot completion.
- ``complete_with_image`` — single-shot completion with an image (vision);
  brand extraction uses it to show Claude a screenshot of the client's site.
- ``analyze_url`` — an agentic call that gives Claude the server-side
  ``web_fetch`` tool so it visits the URL itself, reads the page, and answers.

Reads the API key + model from settings (never hardcoded). The ``anthropic``
SDK is imported lazily so the app runs without it installed / without a key.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.exceptions import ServiceUnavailableError

# Latest web-fetch server tool (Opus 4.8/4.7/4.6, Sonnet 5/4.6). No beta header.
_WEB_FETCH_TOOL_TYPE = "web_fetch_20260209"
_MAX_PAUSE_CONTINUATIONS = 6


class AnthropicClient:
    def __init__(self) -> None:
        self._settings = get_settings().ai

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    def _new_client(self):
        if not self.is_configured:
            raise ServiceUnavailableError("AI provider is not configured.")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - optional dep
            raise ServiceUnavailableError("Anthropic SDK is not installed.") from exc
        return AsyncAnthropic(api_key=self._settings.api_key)

    async def complete(
        self, *, system: str, prompt: str, max_tokens: int | None = None
    ) -> str:
        client = self._new_client()
        message = await client.messages.create(
            model=self._settings.model,
            max_tokens=max_tokens or self._settings.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return _text_of(message)

    async def complete_with_image(
        self,
        *,
        system: str,
        prompt: str,
        image: bytes,
        media_type: str = "image/jpeg",
        max_tokens: int | None = None,
    ) -> str:
        """Single-shot completion where Claude also *sees* an image (vision).

        Used by brand extraction to show the model a screenshot of the site.
        """
        import base64

        client = self._new_client()
        message = await client.messages.create(
            model=self._settings.model,
            max_tokens=max_tokens or self._settings.max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.b64encode(image).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return _text_of(message)

    async def analyze_url(
        self, *, system: str, prompt: str, url: str, max_tokens: int | None = None
    ) -> str:
        """Let Claude fetch ``url`` (via the web_fetch tool) and analyze it.

        The URL is placed in the prompt (web_fetch only fetches URLs already in
        the conversation) and fetching is scoped to that URL's host.
        """
        client = self._new_client()
        tool: dict = {
            "type": _WEB_FETCH_TOOL_TYPE,
            "name": "web_fetch",
            "max_uses": 3,
        }
        host = urlparse(url).hostname
        if host:
            tool["allowed_domains"] = [host]

        messages: list[dict] = [{"role": "user", "content": prompt}]
        for _ in range(_MAX_PAUSE_CONTINUATIONS):
            message = await client.messages.create(
                model=self._settings.model,
                max_tokens=max_tokens or self._settings.max_tokens,
                system=system,
                tools=[tool],
                messages=messages,
            )
            # Server-tool loop hit its cap — resume without adding a user turn.
            if message.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": message.content})
                continue
            return _text_of(message)
        return ""


def _text_of(message) -> str:
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
