# `app/integrations/embeddings`

Pluggable text-embedding backends for the client RAG layer. Claude has no
first-party embeddings API, so this is provider-agnostic behind the
`EmbeddingClient` protocol (`base.py`).

- `voyage.py` — Voyage AI (Anthropic's recommended partner); `voyageai` imported
  lazily. Default in production.
- `fake.py` — deterministic local hash embedder; no key/network. Used
  automatically when unconfigured and in tests, so the pipeline always runs.
- `factory.py` — `get_embedder()` picks the backend from settings.

Configure via `INTEL_*` (see `app/core/config/intelligence.py`). The embedding
dimension must match the model and the `knowledge_chunks.embedding` column
(default 1024; changing it needs a migration).
