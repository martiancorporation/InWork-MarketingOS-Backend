"""Embedding backend contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingClient(Protocol):
    """Turns text into fixed-dimension vectors for similarity search."""

    @property
    def dim(self) -> int: ...

    @property
    def is_configured(self) -> bool: ...

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        """Embed a batch of texts. ``input_type`` is "document" or "query"."""
        ...
