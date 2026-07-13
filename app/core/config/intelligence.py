"""Client-intelligence settings (async build pipeline + RAG). Reads INTEL_* vars.

Controls the post-onboarding pipeline that builds a per-client knowledge profile
(summary + prioritized directives) and a vector RAG layer. Degrades gracefully:
when the embedding provider is unconfigured a deterministic local embedder is
used, and when Claude is unconfigured the agents fall back to a structured,
deterministic profile — so the pipeline always produces *something* usable.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class IntelligenceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="INTEL_",
        extra="ignore",
        case_sensitive=False,
    )

    # Master switch. When off, onboarding does not enqueue build jobs.
    enabled: bool = True  # INTEL_ENABLED

    # ---- embeddings ----
    # Provider: "voyage" (default, Anthropic's recommended partner) or "fake"
    # (deterministic local hash embedder — used automatically when no key).
    embedding_provider: str = "voyage"  # INTEL_EMBEDDING_PROVIDER
    embedding_model: str = "voyage-3"  # INTEL_EMBEDDING_MODEL
    embedding_dim: int = 1024  # INTEL_EMBEDDING_DIM — must match the model
    voyage_api_key: str | None = None  # INTEL_VOYAGE_API_KEY

    # ---- chunking / retrieval ----
    chunk_chars: int = 2400  # ~600 tokens
    chunk_overlap_chars: int = 320
    retrieval_top_k: int = 8  # INTEL_RETRIEVAL_TOP_K

    # ---- budgets / safety ----
    max_corpus_chars: int = 400_000  # cap the corpus fed to the summary agent
    max_document_chars: int = 200_000  # cap extracted text per document

    @property
    def embeddings_configured(self) -> bool:
        return self.embedding_provider == "fake" or bool(self.voyage_api_key)
