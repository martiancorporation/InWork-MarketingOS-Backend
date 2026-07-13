"""Select the embedding backend from settings.

Returns the configured provider (Voyage) when a key is present; otherwise the
deterministic local embedder, so the pipeline degrades gracefully and tests run
without network or credentials.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.integrations.embeddings.base import EmbeddingClient
from app.integrations.embeddings.fake import FakeEmbedder
from app.integrations.embeddings.voyage import VoyageEmbedder


def get_embedder() -> EmbeddingClient:
    s = get_settings().intelligence
    if s.embedding_provider == "voyage" and s.voyage_api_key:
        return VoyageEmbedder(
            api_key=s.voyage_api_key, model=s.embedding_model, dim=s.embedding_dim
        )
    return FakeEmbedder(dim=s.embedding_dim)
