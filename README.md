# InWork MarketingOS — Backend

FastAPI backend for the InWork MarketingOS platform: a multi-client marketing
operations system with AI-assisted client onboarding, a per-client intelligence
(RAG) profile, an AI dashboard (health scores, executive briefs, watchdog,
recommendations), a marketing calendar with an approval workflow, a shared
inbox, a compliance register, analytics, reporting, and a global file-upload
service.

---

## Table of contents
- [Stack](#stack)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Configuration & environments](#configuration--environments)
- [Run it locally](#run-it-locally)
- [API overview](#api-overview)
- [Security model](#security-model)
- [The intelligence pipeline & worker](#the-intelligence-pipeline--worker)
- [AI layer](#ai-layer)
- [Database & migrations](#database--migrations)
- [Testing](#testing)
- [Code quality, CI & tooling](#code-quality-ci--tooling)
- [Deployment](#deployment)
- [Common commands](#common-commands)
- [Conventions (house rules)](#conventions-house-rules)

---

## Stack
- **Python 3.11+** (containers run 3.12)
- **FastAPI** — web framework
- **Pydantic v2 / pydantic-settings** — validation & typed configuration
- **SQLAlchemy 2.0 + Alembic** — ORM & database migrations
- **PostgreSQL** (Neon-compatible) — primary datastore; **pgvector** for RAG
- **Anthropic (Claude)** — the AI layer (brand extraction, health scores,
  recommendations, briefs, watchdog, client intelligence)
- **Voyage AI** — optional embeddings provider (falls back to a local embedder)
- **boto3 / Amazon S3** — object storage for uploads
- **Playwright (headless Chromium)** — renders client sites for brand extraction
- **gunicorn + uvicorn workers** — production server
- **pytest** — tests

All third-party dependencies are **pinned** in [`requirements.txt`](requirements.txt)
for reproducible builds.

---

## Architecture

A request flows in **one direction** through cleanly separated layers:

```
HTTP → app/api/v1/routers  (thin: parse, authorize, delegate)
         → app/services     (business logic; OWNS the transaction/commit)
           → app/repositories (data access only; queries + flush, never commits)
             → PostgreSQL
   schemas (app/schemas) validate the edges • models (app/models) describe the data
```

Supporting pillars:
- **`app/core/`** — cross-cutting concerns: composed settings, security (JWT +
  password hashing), centralized exception handling, request-id log correlation,
  audit middleware, rate limiting, pagination.
- **`app/integrations/`** — thin clients for external systems (Anthropic, AWS S3,
  Voyage embeddings, document extractors, and OAuth stubs for Google/Meta/LinkedIn).
- **`app/ai/`** — AI feature orchestration built on top of `integrations` + `prompts`.
- **`app/services/intelligence/`** — the async client-intelligence (RAG) pipeline,
  driven by a durable job queue and a standalone worker process.
- **`app/prompts/`** — LLM prompts live as data (grouped by feature), never inlined
  in Python.

Key design properties:
- **Object-level authorization** everywhere (not just "is authenticated"). See
  [Security model](#security-model).
- **Transactional discipline:** repositories flush; services commit. Multi-step
  operations are atomic.
- **Graceful degradation:** the app runs without an Anthropic key, without S3,
  and without a Voyage key — each feature falls back to a deterministic path.

---

## Project layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI entry point — assembles app, CORS, middleware, error handlers. |
| `app/core/` | Config, security, exceptions, middleware, logging, rate limiting, request context, pagination. |
| `app/core/config/` | One small `BaseSettings` per concern (app, database, security, ai, storage, intelligence, integrations). |
| `app/api/v1/routers/` | HTTP layer — one router per feature. |
| `app/api/deps.py` | Shared FastAPI dependencies (`CurrentUser`, `AdminUser`, `DbSession`, `Pagination`, `StorageDep`). |
| `app/models/` | SQLAlchemy 2.0 ORM models + `enums.py`. |
| `app/schemas/` | Pydantic request/response models. |
| `app/services/` | Business logic & orchestration. |
| `app/services/intelligence/` | RAG pipeline: orchestrator, ingestion, chunking, context, job queue, reconcile. |
| `app/repositories/` | Data-access layer (queries only). |
| `app/integrations/` | External-system clients (anthropic, aws, embeddings, documents, google/meta/linkedin). |
| `app/ai/` | AI feature modules (brand extraction, dashboard signals, health score, recommendations, watchdog, summary, directives, pricing, usage). |
| `app/prompts/` | Versioned prompt templates, grouped by feature. |
| `app/db/` | Engine, session factory, declarative base, mixins, portable column types. |
| `app/worker.py` | Standalone intelligence worker (`python -m app.worker`). |
| `migrations/` | Alembic migrations. |
| `tests/` | `unit/` and `integration/` suites + `conftest.py`. |
| `scripts/` | Operational scripts (local startup, seeding, docker entrypoint). |
| `.github/workflows/` | CI pipeline. |

---

## Configuration & environments

One variable — **`APP_ENV`** — selects the environment: `local`, `development`,
or `production` (default `local`). Configuration is layered, lowest priority
first:

1. `.env` — shared, non-secret defaults (optional)
2. `.env.<APP_ENV>` — environment-specific values (overrides `.env`)
3. real OS environment variables — always win (how production injects secrets)

**All config enters the app through `app/core/config` — nothing else reads
`os.environ`.** Every `.env*` file is git-ignored; only `*.example` templates are
committed. See [`.env.example`](.env.example) for the full, documented variable
reference.

Guardrails enforced at startup:
- The app **refuses to boot in production** if `SECRET_KEY` is still the dev
  placeholder.
- `SECRET_KEY` must be **≥ 32 characters** in every environment.
- `CORS_ORIGINS` must be explicit — `*` is rejected (incompatible with
  credentialed CORS).

Notable settings groups: `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`,
`CORS_ORIGINS`, `RATE_LIMIT_ENABLED`, `DATABASE_URL` + pool tuning
(`DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`,
`DATABASE_POOL_RECYCLE`), `ANTHROPIC_*` (key, model, max tokens, timeout,
retries), `STORAGE_*` (S3 bucket/region/SSE/limits), and `INTEL_*` (embeddings
provider, kill-switch).

> **Secrets:** never commit real secrets. If a real key ever lands in a working
> `.env`, **rotate it** — git-ignoring a file does not un-expose a leaked key.

---

## Run it locally

### Fastest — one command
```bash
cd Backend
./scripts/run_local.sh          # or: make start
```
Creates the virtualenv, installs deps, generates `.env.local` (with a fresh
`SECRET_KEY`), starts a Docker Postgres, runs migrations, seeds the initial admin,
and launches the server at http://localhost:8000/docs. Idempotent.

### Manual — step by step
**Prerequisites:** Python 3.11+ and PostgreSQL 14+ (or Docker).
```bash
cd Backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                        # or: make install

cp .env.local.example .env.local
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY

docker compose up -d db                                # or: make db-up (or use your own Postgres)
alembic upgrade head                                   # or: make migrate
uvicorn app.main:app --reload                          # or: make run
```

Open **http://localhost:8000/docs** (Swagger UI) or `GET /health` for liveness.

### Quick smoke test
```bash
# Log in as the seeded admin (returns an access token)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@inwork.com","password":"12345678"}'

# List clients (paginated)
curl "http://localhost:8000/api/v1/clients?page=1&page_size=20" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

### The intelligence worker
Client-intelligence builds run **asynchronously** in a separate process:
```bash
python -m app.worker
```
Scales horizontally — each instance claims different jobs via
`FOR UPDATE SKIP LOCKED` + a per-client advisory lock.

---

## API overview

All routes are under `API_V1_PREFIX` (default `/api/v1`). Interactive docs at
`/docs`; schema at `/openapi.json`. Every response uses a consistent error
envelope:

```json
{ "error": { "code": "not_found", "message": "…", "details": null } }
```

Each response also carries an `X-Request-ID` header for log correlation.

| Router | Base path | Highlights |
| --- | --- | --- |
| **auth** | `/auth` | `POST /login` (rate-limited). No sign-up — admins provision users. |
| **users** | `/users` | Admin user management. |
| **clients** | `/clients` | List (paginated), atomic + progressive onboarding wizard, AI brand extraction, detail, admin update. |
| **assignments** | `/clients/{id}/assignments` | Assign users to clients (drives non-admin access). |
| **calendar** | `/clients/{id}/calendar/events` | Events (paginated) + client-approval workflow. |
| **conversations** | `/clients/{id}/conversations` | Shared inbox: threads/messages, folders, stars (paginated). |
| **compliance** | `/clients/{id}/compliance` | Additive compliance register (paginated); changes trigger a rebuild. |
| **analytics** | `/clients/{id}/analytics` | Daily-facts ingest, raw series (paginated), aggregated summary. |
| **reports** | `/clients/{id}/reports` | Report registry/history (paginated). |
| **ai** (dashboard) | `/clients/{id}/dashboard` | Health/brief/watchdog/recommendations (rate-limited); rec decisions. |
| **intelligence** | `/clients/{id}` | Profile/build status, directives, RAG context (admin-only debug). |
| **uploads** | `/uploads` | Global S3-backed file upload / presigned download / delete. |
| **ai_usage** | `/ai-usage` | Token/cost usage events (observability). |
| **audit** | `/audit` | Audit-log read API. |
| **health** | `/health` | Liveness check (unversioned). |

**Pagination:** list endpoints accept `?page=` (≥1) and `?page_size=` (1–100,
default 20) and return `{ items, total, page, page_size }`. Large collections
(calendar, analytics series) that previously returned everything are now
bounded — request larger `page_size` or page through.

**Rate limiting:** in-process sliding-window limiter on `/auth/login` and the
paid-AI routes (`RATE_LIMIT_ENABLED=true`). It is **per-worker** — for exact
global limits behind multiple workers, back it with a shared store (Redis).

---

## Security model

- **Authentication:** JWT bearer tokens (HS256), issued by `POST /auth/login`.
  Passwords are hashed with **SHA-256 pre-hash → bcrypt** (avoids bcrypt's
  72-byte truncation). Login returns a generic error for both unknown email and
  wrong password (no user enumeration).
- **Roles:** `admin`, `manager`, `user`.
- **Object-level authorization (anti-IDOR):** admins see everything; non-admins
  see only clients **assigned** to them. An inaccessible client returns **404,
  not 403**, so IDs can't be probed. Uploads are owner-scoped the same way.
- **SSRF protection:** the brand-extraction fetchers (`app/utils/web.py`,
  `app/utils/render.py`) resolve and reject private/loopback/link-local/reserved
  IPs, and **re-validate every redirect hop** (no auto-follow) so a public URL
  can't 302-redirect into cloud metadata (`169.254.169.254`). The Playwright path
  aborts in-browser requests to private-IP literals.
- **Uploads:** content-type allow-list, size cap, filename sanitization (path
  traversal defeated), private objects + short-lived presigned URLs, SSE-at-rest.
- **Auditing:** the `AuditMiddleware` records every API request (actor, action,
  status, duration, ip) on its own session; failures never break the request.

> **Not yet implemented:** server-side token revocation / logout. Tokens are
> stateless until expiry (`ACCESS_TOKEN_EXPIRE_MINUTES`). The `UserSession` model
> exists as scaffolding for a future refresh-token/revocation flow.

---

## The intelligence pipeline & worker

Each client has an async-built **intelligence profile** (a summary + directives +
a pgvector RAG store) that grounds the AI features.

- **Enqueue (transactional outbox):** onboarding/compliance changes enqueue a
  build job in the *same* transaction, so a committed change always has its job.
  Rapid autosaves are **coalesced** (debounced) into one build.
- **Job queue (`intel_jobs`):** durable queue with retries + exponential backoff
  and dead-lettering after `max_attempts`.
- **Worker (`python -m app.worker`):** polls, claims jobs with
  `FOR UPDATE SKIP LOCKED`, and serializes per-client work with a Postgres
  transaction-scoped **advisory lock** (prevents concurrent profile-version
  races). Runs the full pipeline: document extraction → chunking → embedding →
  profile/summary/directive generation.
- **Embeddings:** Voyage AI when `INTEL_VOYAGE_API_KEY` is set; otherwise a
  deterministic local embedder (so it runs with zero config and hermetically in
  tests). Kill-switch: `INTEL_ENABLED=false`.

---

## AI layer

- **Provider:** Anthropic Claude, via a thin async wrapper
  ([`app/integrations/anthropic/client.py`](app/integrations/anthropic/client.py))
  with a request timeout and SDK-level retries on 429/5xx.
- **Usage tracking:** every call funnels through one place that records tokens +
  priced cost to `ai_usage_events` (`AI_USAGE_ENABLED`).
- **Brand extraction:** headless-Chromium render (text after JS, computed
  brand colors/fonts, a screenshot for Claude vision) with an httpx scrape
  fallback — both SSRF-guarded.
- **Dashboard:** health score, executive brief, watchdog alerts, and
  recommendations — grounded in the client's intelligence context, with
  deterministic fallbacks when Claude is unconfigured.
- **Prompts** live under `app/prompts/<feature>/` as data, loaded via
  `app/prompts/loader.py`.

---

## Database & migrations

- **SQLAlchemy 2.0** typed models (`Mapped[...]`), UUID primary keys generated
  app-side, timezone-aware timestamps.
- **Portable column types** (`app/db/types.py`): `GUID`, `TZDateTime`,
  `JSONColumn` (JSONB on Postgres), native `pg_enum`, and an `Embedding` type
  that renders as pgvector `vector(dim)` on Postgres and degrades to JSON
  elsewhere (so tests run on SQLite).
- **Connection pool** is tuned from config; sizing rule:
  `(pool_size + max_overflow) × workers` must stay under the DB/pooler ceiling
  (important behind Neon's pooler).
- **Migrations** (Alembic) live in `migrations/versions/` with a deterministic
  naming convention; every migration has an `upgrade` and a `downgrade`.

```bash
make migration m="add widget table"   # autogenerate
make migrate                            # apply (alembic upgrade head)
make downgrade                          # roll back one
```

---

## Testing

```bash
make test            # or: pytest -q
pytest tests/unit    # fast, isolated units
```
- **Hermetic:** the suite pins `APP_ENV=test`, disables audit / usage / rate
  limiting, forces the local embedder, and runs each test against a fresh
  in-memory **SQLite** database with FK enforcement — no network, no real
  Postgres/S3/Anthropic (external services are faked).
- **Coverage:** unit + integration for routers, services, security, uploads,
  the SSRF guard, rate limiting, and the intelligence pipeline, including
  authorization (403/404) paths.
- **Known gap:** because units run on SQLite, Postgres-only behavior (pgvector,
  native enums, `FOR UPDATE SKIP LOCKED`, advisory locks) is exercised by the
  CI **migrations** job against a real Postgres rather than the unit suite.

```bash
pytest --cov=app --cov-report=term-missing   # coverage (config in pyproject.toml)
```

---

## Code quality, CI & tooling

- **Ruff** (lint + format) and **mypy** are configured in
  [`pyproject.toml`](pyproject.toml).
  ```bash
  make lint       # ruff check .
  make format     # ruff format .
  ```
- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on push /
  PR: ruff lint → (advisory) format check → (advisory) mypy → pytest, plus a
  separate job that applies all migrations against a real Postgres service to
  catch model/migration drift.

---

## Deployment

- **Container:** multi-stage [`Dockerfile`](Dockerfile) — deps built in a builder
  stage into a venv, headless Chromium installed, runs as a **non-root** user,
  with a `HEALTHCHECK` on `/health`.
- **Server:** `gunicorn` with uvicorn workers (per-worker supervision, graceful
  timeout).
- **Migrations:** the container entrypoint
  ([`scripts/docker-entrypoint.sh`](scripts/docker-entrypoint.sh)) runs
  `alembic upgrade head` when `RUN_MIGRATIONS=1`, then starts the server.
- **Secrets:** injected via the environment / a secrets manager — never baked
  into the image.

```bash
docker build -t inwork-api .
docker run --env-file .env.production -e RUN_MIGRATIONS=1 -p 8000:8000 inwork-api
```

> The Dockerfile depends on `scripts/docker-entrypoint.sh` being present in the
> build context. Ensure `scripts/` is tracked/available wherever you build.

---

## Common commands

| Command | Does |
| --- | --- |
| `make start` | One-shot local setup + run |
| `make install` | Create venv + install deps |
| `make db-up` / `make db-down` | Start / stop local Postgres container |
| `make migration m="msg"` | Autogenerate a migration |
| `make migrate` / `make downgrade` | Apply / roll back migrations |
| `make run` / `make run-prod` | Dev server (reload) / prod server |
| `make test` | Run the test suite |
| `make lint` / `make format` | Ruff check / format |
| `make seed` | Create the initial admin (idempotent) |
| `python -m app.worker` | Run the intelligence worker |

All commands accept `APP_ENV=<env>`.

---

## Conventions (house rules)

1. **Small, focused files** — one responsibility per file; split by domain and layer.
2. **One-directional layering** — `routers → services → repositories → db`. Routers
   stay thin; business logic lives in services.
3. **Repositories never commit; services own the transaction.** Multi-step writes
   are atomic.
4. **Object-level authorization** on every client-scoped route (404, never 403,
   for inaccessible resources).
5. **No secrets in code** — everything through `app/core/config`.
6. **Prompts are data** — under `app/prompts/`, never inlined.
7. **Bounded inputs** — request models cap free-text length and batch sizes, and
   forbid unknown fields (`StrictModel`) so typos fail loudly.
8. **Bounded outputs** — list endpoints are paginated.
9. **Graceful degradation** — AI/storage/embeddings each have a working fallback.

See [`CLAUDE.md`](CLAUDE.md) for the condensed rules that AI assistants follow in
this repo.
