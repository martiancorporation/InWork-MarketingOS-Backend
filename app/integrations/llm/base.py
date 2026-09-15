"""LLM backend contract — the swappable-provider boundary.

Every ``app/ai/*`` agent and client-scoped service depends on this ``Protocol``
(via ``get_llm_client()`` in ``factory.py``), never on a concrete vendor class.
Swapping the underlying LLM vendor is then a one-file change: add a new
provider module here and point ``factory.py`` at it — nothing under ``app/ai``
or ``app/services`` has to change. Mirrors the same pattern already used for
embeddings (``app/integrations/embeddings/base.py`` + ``factory.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.ai.usage import AiUsageContext


@runtime_checkable
class LLMClient(Protocol):
    """A chat-completion backend: plain, vision, and streaming completions."""

    @property
    def is_configured(self) -> bool: ...

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
    ) -> str: ...

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
    ) -> str: ...

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
    ) -> str: ...

    def stream(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas from a streaming completion."""
        ...
