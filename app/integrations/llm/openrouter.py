"""Async client for OpenRouter's OpenAI-compatible Chat Completions API.

OpenRouter proxies many vendors' models (Anthropic, OpenAI, Google, Meta, ...)
behind one API key and one ``/chat/completions`` endpoint, so this client is
implemented over ``httpx`` (already a dependency) rather than a vendor SDK —
same approach as ``app/integrations/openai/client.py``. No extra dependency is
required, and the app runs without a key configured.

Entry points mirror ``LLMClient`` (see ``base.py``):
- ``complete`` — a plain single-shot completion.
- ``complete_with_image`` / ``complete_with_images`` — vision, via OpenAI-style
  ``image_url`` content parts (base64 data URIs).
- ``stream`` — token-by-token completion (SSE), text only.

Every call funnels through ``_invoke`` (or, for ``stream``, an equivalent
inline path), which is the single place usage is recorded: it captures the
response's token usage, prices it, and writes one ``ai_usage_events`` row (see
``app/ai/usage.py``). Callers attribute the call by passing an
``AiUsageContext`` (feature / user / client) — either per-call or as an
instance default.

Reads the API key/model/base URL from settings (never hardcoded).
"""

from __future__ import annotations

import base64
import json
import time
from functools import partial

import anyio
import httpx

from app.ai.pricing import UsageBreakdown
from app.ai.usage import AiUsageContext, record_usage
from app.core.config import get_settings
from app.core.exceptions import ServiceUnavailableError

_PROVIDER = "openrouter"


class OpenRouterClient:
    def __init__(self, context: AiUsageContext | None = None) -> None:
        self._settings = get_settings().ai
        self._context = context  # optional instance-wide attribution default

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        # Optional OpenRouter attribution headers — show up on the dashboard,
        # never required for the API to work.
        if self._settings.site_url:
            headers["HTTP-Referer"] = self._settings.site_url
        if self._settings.app_name:
            headers["X-Title"] = self._settings.app_name
        return headers

    def _new_http_client(self) -> httpx.AsyncClient:
        # httpx retries connection-level failures only (not 429/5xx status —
        # that would need response-aware backoff, out of scope here); this is
        # a partial but real mapping of the configured retry budget.
        transport = httpx.AsyncHTTPTransport(retries=self._settings.max_retries)
        return httpx.AsyncClient(timeout=self._settings.timeout_seconds, transport=transport)

    async def _post(self, payload: dict) -> dict:
        if not self.is_configured:
            raise ServiceUnavailableError("AI provider is not configured.")
        url = self._settings.base_url.rstrip("/") + "/chat/completions"
        async with self._new_http_client() as http:
            resp = await http.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def _invoke(
        self, payload: dict, *, operation: str, context: AiUsageContext | None
    ) -> dict:
        """Run one Chat Completions call and record its token usage + cost.

        Records on success (with real usage) and on failure (status=error, zero
        usage) so every attempt is accounted for. ``record_usage`` does a
        synchronous DB commit, so it's offloaded to a worker thread
        (``anyio.to_thread.run_sync``) rather than called directly — every
        caller of this method is ``async``, and calling it inline would block
        the event loop on every single AI call, platform-wide.
        """
        ctx = context or self._context
        model = payload.get("model", self._settings.model)
        started = time.perf_counter()
        try:
            body = await self._post(payload)
        except Exception as exc:
            await anyio.to_thread.run_sync(
                partial(
                    record_usage,
                    context=ctx,
                    provider=_PROVIDER,
                    model=model,
                    operation=operation,
                    usage=None,
                    status="error",
                    error=str(exc)[:500],
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            # Translate to a typed error rather than leaking the raw httpx
            # exception past this client — every current caller already wraps
            # this in a broad `except Exception` and degrades gracefully.
            raise ServiceUnavailableError(f"AI provider request failed: {exc}") from exc
        await anyio.to_thread.run_sync(
            partial(
                record_usage,
                context=ctx,
                provider=_PROVIDER,
                model=model,
                operation=operation,
                usage=_usage_from_body(body),
                status="success",
                request_id=str(body.get("id") or "") or None,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )
        return body

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
    ) -> str:
        body = await self._invoke(
            {
                "model": model or self._settings.model,
                "max_tokens": max_tokens or self._settings.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            operation="complete",
            context=context,
        )
        return _text_of(body)

    async def complete_with_image(
        self,
        *,
        system: str,
        prompt: str,
        image: bytes,
        media_type: str = "image/jpeg",
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
    ) -> str:
        """Single-shot completion where the model also *sees* an image (vision)."""
        return await self.complete_with_images(
            system=system,
            prompt=prompt,
            images=[(image, media_type)],
            max_tokens=max_tokens,
            model=model,
            context=context,
            operation="complete_with_image",
        )

    async def complete_with_images(
        self,
        *,
        system: str,
        prompt: str,
        images: list[tuple[bytes, str]],
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
        operation: str = "complete_with_images",
    ) -> str:
        """Vision completion over one *or more* images.

        Images come first and the question last, same ordering rationale as the
        previous provider: the text can then refer to them in order. ``operation``
        is only the usage-log label, so the single-image caller keeps its
        historical name in ``ai_usage_events`` rather than silently changing.
        """
        content: list[dict] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
                },
            }
            for data, media_type in images
        ]
        content.append({"type": "text", "text": prompt})

        body = await self._invoke(
            {
                "model": model or self._settings.model,
                "max_tokens": max_tokens or self._settings.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            },
            operation=operation,
            context=context,
        )
        return _text_of(body)

    async def stream(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
    ):
        """Yield text deltas from a streaming completion (ChatGPT-style typing).

        An async generator: ``async for delta in client.stream(...)``. Usage is
        recorded once when the stream closes, from the accumulated usage chunk
        OpenRouter sends at the end (``stream_options.include_usage``) — the
        same accounting as ``_invoke``, just deferred until the last token.
        """
        if not self.is_configured:
            raise ServiceUnavailableError("AI provider is not configured.")
        ctx = context or self._context
        model = model or self._settings.model
        payload = {
            "model": model,
            "max_tokens": max_tokens or self._settings.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        url = self._settings.base_url.rstrip("/") + "/chat/completions"
        started = time.perf_counter()
        usage: UsageBreakdown | None = None
        request_id: str | None = None
        try:
            async with self._new_http_client() as http:
                async with http.stream("POST", url, json=payload, headers=self._headers()) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if not data or data == "[DONE]":
                            continue
                        chunk = json.loads(data)
                        request_id = chunk.get("id") or request_id
                        if chunk.get("usage"):
                            usage = _usage_from_body(chunk)
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        text = (choices[0] or {}).get("delta", {}).get("content")
                        if text:
                            yield text
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
            usage=usage,
            status="success",
            request_id=request_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _text_of(body: dict) -> str:
    choices = body.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    return str(message.get("content") or "")


def _usage_from_body(body: dict) -> UsageBreakdown:
    u = body.get("usage") or {}
    return UsageBreakdown(
        input_tokens=int(u.get("prompt_tokens", 0) or 0),
        output_tokens=int(u.get("completion_tokens", 0) or 0),
    )
