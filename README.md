# InWork MarketingOS — Backend

FastAPI backend for the InWork MarketingOS platform.

## Stack
- **Python 3.11+**
- **FastAPI** — web framework
- **Pydantic v2 / pydantic-settings** — validation & typed configuration
- **SQLAlchemy + Alembic** — ORM & database migrations (added later)
- **Anthropic (Claude)** — the AI layer (health scores, recommendations, briefs)

## Guiding principles
1. **Small, focused files.** One responsibility per file. No 300–400 line files —
   split by domain and by layer instead.
2. **Layered architecture.** A request flows in one direction:
   `api (routers) → services → repositories → database`.
   Schemas validate the edges, models describe the data.
3. **No secrets in code.** Every key, credential, or environment value is read
   through `app/core/config`. Real values live only in a local `.env`
   (git-ignored). See `.env.example` for the required variable names.
4. **Prompts are data, not code.** All LLM prompts live under `app/prompts/`,
   organized by feature — never inlined in Python.
5. **Every folder documents itself.** Each folder has a `README.md` explaining
   what belongs there so any developer or AI agent can contribute safely.

## Top-level layout
| Path | Purpose |
| --- | --- |
| `app/` | The application package (all backend code). |
| `app/main.py` | FastAPI entry point — assembles the app. |
| `app/core/` | Cross-cutting concerns: config, security, logging, errors. |
| `app/api/` | HTTP layer — versioned routers only. |
| `app/models/` | Database (ORM) models. |
| `app/schemas/` | Pydantic request/response models. |
| `app/services/` | Business logic & orchestration. |
| `app/repositories/` | Data-access layer (DB queries). |
| `app/integrations/` | Clients for external APIs (Anthropic, Google, Meta…). |
| `app/ai/` | AI orchestration built on top of integrations + prompts. |
| `app/prompts/` | Versioned LLM prompt templates, grouped by feature. |
| `app/tasks/` | Background / scheduled jobs. |
| `app/utils/` | Small, dependency-free helpers. |
| `db/` (in `app`) | Engine, session, and base setup. |
| `migrations/` | Alembic database migrations. |
| `tests/` | Unit & integration tests. |
| `scripts/` | Operational scripts (startup, seeding). |

## Getting started (later)
Actual setup steps (install, migrate, run) are added once implementation begins.
# InWork-MarketingOS-Backend
