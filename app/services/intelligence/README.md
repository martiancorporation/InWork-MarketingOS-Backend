# `app/services/intelligence`

The async, post-onboarding **client intelligence** subsystem: it ingests every
client input (onboarding fields + uploaded files), builds a versioned summary +
a prioritized directive store, and a pgvector RAG layer — then serves that
context to every downstream agent.

Pipeline (runs in `app/worker.py`, off the request path):

`ingestion_service` (download S3 files, extract text, content-hash for change
detection) → `chunking_service` + embeddings → `orchestrator` runs the two
agents (`app/ai/summary.py`, `app/ai/directives.py`) → `reconcile` (dedupe,
conflict detection, capability flags) → commits a new `client_profiles` version
and flips `clients.current_profile_version` atomically.

- `job_queue.py` — durable Postgres queue (transactional enqueue + coalescing;
  `FOR UPDATE SKIP LOCKED` claim; retry/dead-letter).
- `orchestrator.py` — the build; full vs incremental (hash-skip unchanged
  sources, always re-reconcile directives over the full corpus).
- `context_service.py` — `build()` returns the always-latest directive preamble
  + capability flags + top-k retrieved chunks for any downstream agent.
- `client_agent.py` — base class all client-scoped agents extend; injects the
  preamble and turns capability flags into hard gates (`ensure_allowed`).
- `intelligence_service.py` — read/management (status, versions, rebuild,
  resolve conflicts) behind the `intelligence` router.

Directives are the enforceable layer (always injected + compiled to flags);
RAG chunks augment with detail. File content is treated as untrusted data.
Degrades gracefully: no embedding key → local embedder; no Claude key →
deterministic field-based profile.
