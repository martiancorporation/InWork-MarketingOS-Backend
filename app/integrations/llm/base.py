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
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.ai.usage import AiUsageContext


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked to make, from a tool-calling turn."""

    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolCallResponse:
    """The result of one ``complete_with_tools`` round.

    ``content`` is the model's natural-language reply (set when it chose not
    to call a tool, or alongside tool calls for some providers). ``tool_calls``
    is empty when the model returned a final answer with nothing left to do.
    """

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class StreamDelta:
    """One chunk from ``stream_with_tools``: either a text token (as it
    arrives) or one fully-accumulated tool call. ``tool_call`` is only ever
    emitted once its JSON arguments are complete — never a partial one — so a
    caller can dispatch it immediately without its own buffering."""

    text: str | None = None
    tool_call: ToolCall | None = None


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

    async def complete_with_tools(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
    ) -> ToolCallResponse:
        """One round of an OpenAI-style tool-calling conversation.

        Unlike ``complete``, this takes the full message history (including any
        prior ``assistant`` tool-call messages and their ``tool`` result
        messages) rather than a single ``system``/``prompt`` pair — a
        tool-calling loop needs to keep replaying that history back to the
        model on every round. ``tools`` is the OpenAI/JSON-Schema function
        list (see ``app/ai/tools/registry.py``). Used only by
        ``app/ai/command_agent.py`` — every other AI feature keeps using
        ``complete``/``stream``.
        """
        ...

    def stream_with_tools(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
        max_tokens: int | None = None,
        model: str | None = None,
        context: AiUsageContext | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Streaming counterpart to ``complete_with_tools`` — the SSE-backed
        turn endpoint's engine. Yields a ``StreamDelta(text=...)`` per token as
        the model's own reply is generated, and a ``StreamDelta(tool_call=...)``
        for each tool call once its arguments are fully accumulated. Used only
        by ``app/ai/command_agent.py``'s streaming entry point.
        """
        ...
