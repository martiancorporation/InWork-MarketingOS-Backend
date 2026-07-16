---
name: verify-changes
description: >-
  Runs this backend's standard quality gate before finishing a change — ruff
  lint, the full pytest suite, and an app-boot / OpenAPI smoke check, all against
  the project venv. Use when asked to verify, validate, check the codebase, or
  confirm a change is safe to hand off / commit.
allowed-tools: Read, Bash
---

# Verify changes (quality gate)

Run the same checks CI runs, locally, against the project venv. Everything must pass before a
change is considered done. Always use `.venv/bin/python` (not a system Python).

## 1. Lint
```
.venv/bin/python -m ruff check app tests
```
Auto-fix the mechanical ones if needed: `.venv/bin/python -m ruff check app tests --fix`.
Config is in `pyproject.toml` (note: `B008` is intentionally ignored — it's the FastAPI
`Depends`/`Query` default idiom).

## 2. Tests (full suite, hermetic)
```
.venv/bin/python -m pytest -q
```
- Expect all green, **no new skips**. The suite is SQLite-backed and offline.
- For a fast inner loop, run the touched file first:
  `.venv/bin/python -m pytest tests/integration/test_<feature>.py -q`.

## 3. App-boot / OpenAPI smoke
Catches import errors, bad router wiring, and response_model mistakes that unit tests miss:
```
APP_ENV=test SECRET_KEY="test-secret-key-must-be-at-least-32-bytes-long-00" \
  .venv/bin/python -c "from app.main import app; app.openapi(); print('boots OK')"
```

## 4. Migration drift (only if models/migrations changed)
Against a real Postgres (pgvector image), confirm migrations apply cleanly:
```
make db-up && make migrate && make downgrade && make migrate
```
Optionally check autogenerate reports no unexpected diff between models and schema.

## 5. Report honestly
State exactly what passed and what didn't — paste the failing output, don't paper over it.
A change is "done" only when lint is clean, the whole suite passes, and the app boots. If a
step was skipped (e.g. no DB available for the migration check), say so.

## Reminders
- Don't commit unless asked; branch off `main` first when you do.
- Rotating real secrets and any deploy/infra step are out of scope for this gate — flag them
  separately.
