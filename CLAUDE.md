# CLAUDE.md

FastAPI backend for InWork MarketingOS. See @README.md for the full overview.
This file holds the rules and commands an assistant can't infer from the code.

## Commands
- Install: `make install` (creates `.venv`, installs pinned deps)
- Run dev server: `make run` (uvicorn, autoreload)
- Run tests: `make test` (or `pytest -q`); single file: `pytest tests/unit/test_web.py -q`
- Lint / format: `make lint` (`ruff check .`) / `make format` (`ruff format .`)
- Migrations: `make migration m="msg"` (autogenerate), `make migrate` (apply), `make downgrade`
- Intelligence worker: `python -m app.worker`
- Always run against the project venv: `.venv/bin/python -m pytest`

## Architecture (one-directional layering)
`app/api/v1/routers → app/services → app/repositories → PostgreSQL`
- Routers are **thin**: parse input, enforce auth, delegate. No business logic.
- Schemas (`app/schemas`) validate the HTTP edges; models (`app/models`) describe data.
- `app/services/intelligence/` is the async RAG pipeline; `app/worker.py` drains its queue.

## House rules (enforce on every change)
- **Repositories never commit.** Services own the transaction/commit. Keep multi-step writes atomic.
- **Object-level authorization** on every client-scoped route: admins see all;
  non-admins only see assigned clients. Return **404, never 403**, for an inaccessible
  resource (so IDs can't be probed). Follow the existing `ClientService.get_client` pattern.
- **No secrets in code and nothing reads `os.environ`** except `app/core/config`. Add new
  settings to the relevant `app/core/config/*.py` module and document them in `.env.example`.
- **Prompts are data:** put LLM prompts under `app/prompts/<feature>/`, never inline in Python.
- **Bound all inputs:** request schemas cap free-text (`max_length`, see `MAX_TEXT`/`MAX_LONG_LINE`
  in `app/schemas/common.py`), cap list/batch sizes, and forbid unknown fields (inherit
  `StrictModel`, i.e. `extra="forbid"`).
- **Bound all outputs:** list endpoints must paginate via the shared `Pagination` dependency and
  return `{ items, total, page, page_size }`. Push `LIMIT`/`OFFSET` + a count to the repository.
- **Errors:** raise typed exceptions from `app/core/exceptions.py` (`NotFoundError`, `AuthError`,
  `ForbiddenError`, `ConflictError`, `TooManyRequestsError`, …). Never build HTTP responses in
  services. The central handlers produce the `{"error": {...}}` envelope.
- **Graceful degradation:** AI (Anthropic), storage (S3), and embeddings (Voyage) must each keep
  working via their deterministic fallback when unconfigured — don't hard-require them.
- **Async routes must not block the event loop:** if an `async def` handler calls sync I/O
  (S3, blocking DB), offload it with `anyio.to_thread.run_sync`. Plain `def` handlers are fine
  (FastAPI runs them in a threadpool).

## Adding a client-scoped resource
Follow the existing vertical slice (model → migration → schema → repository → service → router,
+ tests). The `add-resource` skill (`.claude/skills/add-resource/`) documents the full workflow.

## Conventions
- Python 3.11+ typing (`X | None`, `list[...]`), `from __future__ import annotations` at top.
- Ruff config in `pyproject.toml`. `B008` is intentionally ignored (it's the FastAPI
  `Depends(...)`/`Query(...)` default idiom — that pattern is correct here, not a bug).
- Rate-limit sensitive/paid routes with `Depends(RateLimit(...))` from `app/core/rate_limit.py`.

## Gotchas
- **Tests run on in-memory SQLite**, not Postgres. Postgres-only behavior (pgvector, native enums,
  `FOR UPDATE SKIP LOCKED`, advisory locks) is code-guarded (`dialect == "postgresql"`) and is
  covered by the CI migrations job, not the unit suite. Don't assume a query behaves identically
  on both — test Postgres paths in CI or manually.
- The hermetic suite pins `APP_ENV=test` and disables audit, ai-usage, and rate limiting in
  `tests/conftest.py`. Keep new global side-effects behind an env flag so tests stay hermetic.
- `SECRET_KEY` must be ≥32 chars and `CORS_ORIGINS` may not be `*` — config validation rejects both.
- Token revocation is **not** implemented; JWTs are stateless until expiry. `UserSession` is unused
  scaffolding — don't assume logout works.

## Do not
- Do not commit real secrets or point non-prod env files at the production database.
- Do not add unpinned dependencies — pin exact versions in `requirements.txt`.
- Do not commit unless asked; branch off `main` first when you do.
