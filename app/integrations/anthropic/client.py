"""Thin async wrapper around the Anthropic (Claude) Messages API.

Three entry points:
- ``complete`` — a plain single-shot completion.
- ``complete_with_image`` — single-shot completion with an image (vision);
  brand extraction uses it to show Claude a screenshot of the client's site.
- ``analyze_url`` — an agentic call that gives Claude the server-side
  ``web_fetch`` tool so it visits the URL itself, reads the page, and answers.

Every call funnels through ``_invoke``, which is the single place usage is
recorded: it captures the response's token usage, prices it, and writes one
``ai_usage_events`` row (see ``app/ai/usage.py``). Callers attribute the call by
passing an ``AiUsageContext`` (feature / user / client) — either per-call or as
an instance default. This is the "instrument once, track everywhere" point.

Reads the API key + model from settings (never hardcoded). The ``anthropic``
SDK is imported lazily so the app runs without it installed / without a key.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from app.ai.usage import AiUsageContext, record_usage, usage_from_message
from app.core.config import get_settings
from app.core.exceptions import ServiceUnavailableError

# Latest web-fetch server tool (Opus 4.8/4.7/4.6, Sonnet 5/4.6). No beta header.
_WEB_FETCH_TOOL_TYPE = "web_fetch_20260209"
_MAX_PAUSE_CONTINUATIONS = 6
_PROVIDER = "anthropic"


class AnthropicClient:
    def __init__(self, context: AiUsageContext | None = None) -> None:
        self._settings = get_settings().ai
        self._context = context  # optional instance-wide attribution default

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
        # timeout bounds each request; max_retries lets the SDK retry transient
        # 429/5xx with exponential backoff (no unbounded hangs, no manual loop).
        return AsyncAnthropic(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
            max_retries=self._settings.max_retries,
        )

    async def _invoke(self, create_kwargs: dict, *, operation: str, context: AiUsageContext | None):
        """Run one Messages call and record its token usage + cost.

        Records on success (with real usage) and on failure (status=error, zero
        usage) so every attempt is accounted for.
        """
        client = self._new_client()
        ctx = context or self._context
        model = create_kwargs.get("model", self._settings.model)
        started = time.perf_counter()
        try:
            message = await client.messages.create(**create_kwargs)
        except Exception as exc:
            record_usage(
                context=ctx,
                provider=_PROVIDER,
                model=model,
                operation=operation,
                usage=None,
                status="error",
                error=str(exc)[:500],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        record_usage(
            context=ctx,
            provider=_PROVIDER,
            model=model,
            operation=operation,
            usage=usage_from_message(message),
            status="success",
            request_id=getattr(message, "id", None),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return message

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        context: AiUsageContext | None = None,
    ) -> str:
        message = await self._invoke(
            {
                "model": self._settings.model,
                "max_tokens": max_tokens or self._settings.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            operation="complete",
            context=context,
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
        context: AiUsageContext | None = None,
    ) -> str:
        """Single-shot completion where Claude also *sees* an image (vision)."""
        import base64

        message = await self._invoke(
            {
                "model": self._settings.model,
                "max_tokens": max_tokens or self._settings.max_tokens,
                "system": system,
                "messages": [
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
            },
            operation="complete_with_image",
            context=context,
        )
        return _text_of(message)

    async def stream(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        context: AiUsageContext | None = None,
    ):
        """Yield text deltas from a streaming completion (ChatGPT-style typing).

        An async generator: ``async for delta in client.stream(...)``. Usage is
        recorded once when the stream closes, from the final message — the same
        accounting as ``_invoke``, just deferred until the last token.
        """
        client = self._new_client()
        ctx = context or self._context
        model = self._settings.model
        started = time.perf_counter()
        final = None
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens or self._settings.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        except Exception as exc:
            record_usage(
                context=ctx,
                provider=_PROVIDER,
                model=model,
                operation="stream",
                usage=None,
                status="error",
                error=str(exc)[:500],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        record_usage(
            context=ctx,
            provider=_PROVIDER,
            model=model,
            operation="stream",
            usage=usage_from_message(final) if final is not None else None,
            status="success",
            request_id=getattr(final, "id", None),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def analyze_url(
        self,
        *,
        system: str,
        prompt: str,
        url: str,
        max_tokens: int | None = None,
        context: AiUsageContext | None = None,
    ) -> str:
        """Let Claude fetch ``url`` (via the web_fetch tool) and analyze it.

        Each Messages round-trip (including pause_turn continuations) goes
        through ``_invoke``, so each is recorded as its own usage event.
        """
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
            message = await self._invoke(
                {
                    "model": self._settings.model,
                    "max_tokens": max_tokens or self._settings.max_tokens,
                    "system": system,
                    "tools": [tool],
                    "messages": messages,
                },
                operation="analyze_url",
                context=context,
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
