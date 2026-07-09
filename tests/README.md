# tests/

Automated tests for the backend.

- `unit/` — fast, isolated tests for a single function/service (no DB, no network).
- `integration/` — tests that exercise multiple layers (API + services + DB).
- `fixtures/` — shared sample data and pytest fixtures.

`conftest.py` holds fixtures shared across the whole suite. `helpers.py` has
payload builders (e.g. `onboarding_payload`).

## Running
```bash
make test              # or: .venv/bin/python -m pytest
.venv/bin/python -m pytest tests/integration/test_flow.py   # a single file
.venv/bin/python -m pytest -k rbac                          # by keyword
```

## How it works (hermetic — no external services)
- Each test gets a **fresh in-memory SQLite** database (the models are
  dialect-portable), created via a `db_session` fixture and wired in by
  overriding the `get_db` dependency — no Postgres required.
- Config is pinned to a `test` environment in `conftest.py` **before** the app
  imports, so `.env.*` files never leak in. `ANTHROPIC_API_KEY` is forced empty
  so the AI path never makes a network call; the one test that covers the
  AI-configured branch monkeypatches the Anthropic client.

## Fixtures
- `client` — a `TestClient` bound to the fresh test DB.
- `admin_headers` — bootstraps the first user (admin) and returns its auth header.
- `make_user(email, role, …)` — admin-creates a user and returns
  `(user_json, that_user's_auth_header)`.

## Files
| File | Covers |
| --- | --- |
| `unit/test_security.py` | password hashing, JWT create/decode/expiry |
| `unit/test_slug.py` | slug generation & uniqueness |
| `integration/test_auth.py` | bootstrap signup, login, disabled account, validation |
| `integration/test_users.py` | admin user CRUD, RBAC, duplicates, validation |
| `integration/test_clients.py` | onboarding, listing/search/pagination, brand extraction |
| `integration/test_assignments.py` | assign / unassign / list, RBAC, 404s |
| `integration/test_flow.py` | **the complete end-to-end RBAC process flow** |
