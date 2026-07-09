# InWork MarketingOS — Backend

FastAPI backend for the InWork MarketingOS platform.

## Stack
- **Python 3.11+**
- **FastAPI** — web framework
- **Pydantic v2 / pydantic-settings** — validation & typed configuration
- **SQLAlchemy 2.0 + Alembic** — ORM & database migrations
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

## Environments

One variable — `APP_ENV` — selects the environment: **`local`**, **`development`**,
or **`production`** (default `local`). Configuration is layered, lowest priority
first:

1. `.env` — shared, non-secret defaults (optional)
2. `.env.<APP_ENV>` — environment-specific values (overrides `.env`)
3. real OS environment variables — always win (how production injects secrets)

Every `.env*` file is git-ignored; only the `*.example` templates are committed:

| Environment | Template to copy | Real file (git-ignored) |
| --- | --- | --- |
| Local | `.env.local.example` | `.env.local` |
| Development | `.env.development.example` | `.env.development` |
| Production | `.env.production.example` | `.env.production` |
| Reference (all vars) | `.env.example` | — |

Switch environments with a single variable, e.g. `APP_ENV=production make run-prod`.
As a safety net, the app **refuses to start in production** if `SECRET_KEY` is
still the development placeholder.

## Run it locally

### Fastest — one command
```bash
cd Backend
./scripts/run_local.sh          # or: make start
```
That's it. The script creates the virtualenv, installs dependencies, generates
`.env.local` (with a fresh `SECRET_KEY`), starts a Docker Postgres, waits for it,
runs migrations, and launches the server at http://localhost:8000/docs. It is
idempotent — run it again any time. (Requires Python 3.11+ and Docker; if you
don't use Docker, point `DATABASE_URL` in `.env.local` at your own Postgres and
re-run.)

### Manual — step by step

**Prerequisites:** Python 3.11+ and a PostgreSQL 14+ database (or Docker).

```bash
cd Backend

# 1. Create & activate a virtual environment
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt      # or: make install

# 3. Set up your local environment file
cp .env.local.example .env.local
#    Generate a SECRET_KEY and paste it in:
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 4. Start a database
#    Option A — Docker (recommended, no local install):
docker compose up -d db              # or: make db-up
#    Option B — use your own local Postgres and set DATABASE_URL in .env.local

# 5. Apply database migrations
alembic upgrade head                                    # or: make migrate

# 6. Run the API (APP_ENV defaults to local)
uvicorn app.main:app --reload        # or: make run
```

> `./scripts/run_local.sh` (or `make start`) does all of this automatically —
> including seeding the initial admin (`admin@inwork.com` / `12345678`).

Open **http://localhost:8000/docs** for interactive API docs, or
`GET http://localhost:8000/health` for a liveness check.

### Quick smoke test
```bash
# Log in as the seeded admin (returns an access token)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@inwork.com","password":"12345678"}'

# List clients (paste the token from above)
curl http://localhost:8000/api/v1/clients \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

## Running other environments

```bash
# Development (points at the shared dev database/config)
cp .env.development.example .env.development   # fill in real values
APP_ENV=development make migrate
APP_ENV=development make run

# Production (secrets come from the environment / a secrets manager)
export APP_ENV=production
export SECRET_KEY="..." DATABASE_URL="..." ANTHROPIC_API_KEY="..."
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4   # or: make run-prod
# Container: docker build -t inwork-api . && docker run --env-file .env.production -p 8000:8000 inwork-api
```

## Common commands (`make help`)
| Command | Does |
| --- | --- |
| `make install` | Install dependencies |
| `make db-up` / `make db-down` | Start / stop the local Postgres container |
| `make migration m="msg"` | Create an autogenerated migration |
| `make migrate` | Apply pending migrations |
| `make run` | Dev server with autoreload |
| `make run-prod` | Production server (4 workers, no reload) |
| `make test` | Run the test suite |

All commands accept `APP_ENV=<env>` to target a specific environment.
