"""Pluggable text-embedding backends for the client RAG layer.

Claude has no first-party embeddings API, so this is provider-agnostic. Default
is Voyage AI (Anthropic's recommended partner); a deterministic local embedder
is used automatically when no key is configured, so the pipeline always runs.
"""

from app.integrations.embeddings.base import EmbeddingClient
from app.integrations.embeddings.factory import get_embedder

__all__ = ["EmbeddingClient", "get_embedder"]
