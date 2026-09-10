"""Select the LLM backend.

The ONE place to change to point the whole app at a different provider: every
``app/ai/*`` agent and client-scoped service calls ``get_llm_client()`` instead
of constructing a vendor client directly, so swapping providers later is a
change here (and a new module implementing ``LLMClient``), not a sweep across
every call site. Today there is exactly one backend (OpenRouter); this factory
is still where a second one would be chosen from, by settings.

Deliberately NOT cached like ``get_embedder()`` — an ``AiUsageContext`` is
often passed per call for attribution, so a shared singleton would leak the
wrong context across callers. Constructing a client is cheap (the HTTP
connection is opened lazily, per request).
"""

from __future__ import annotations

from app.ai.usage import AiUsageContext
from app.integrations.llm.base import LLMClient
from app.integrations.llm.openrouter import OpenRouterClient


def get_llm_client(context: AiUsageContext | None = None) -> LLMClient:
    return OpenRouterClient(context)
