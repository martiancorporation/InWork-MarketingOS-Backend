---
name: coding-style
description: >-
  The coding style and conventions for this FastAPI backend (Python 3.11+
  typing, imports, naming, docstrings, layering idioms, error handling, config).
  Use when writing or refactoring any Python in app/ so new code matches the
  existing codebase.
allowed-tools: Read, Grep, Glob
---

# Coding style & conventions

Write code that reads like the file next to it. When unsure, open a sibling in the
same layer and mirror it.

## Python
- Target **Python 3.11+**. Use modern typing: `X | None`, `list[...]`, `dict[str, X]`
  — never `Optional`, `List`, `Dict` from `typing`.
- Put `from __future__ import annotations` at the top of every module.
- Fully type function signatures (params + return). Prefer `Mapped[...]` on ORM columns.
- Small, focused files — one responsibility each. Split by domain and layer rather than
  growing a file past ~300 lines.

## Imports
- Order: stdlib → third-party → first-party (`app.*`). Ruff's isort (`I`) enforces this;
  run `make format`.
- No wildcard imports. Import the symbol, not the module, when it reads cleaner locally.

## Naming
- `snake_case` for functions/variables/modules, `PascalCase` for classes, `UPPER_SNAKE`
  for module-level constants.
- Files: `<feature>.py` per layer (e.g. `compliance.py` router / service / repository).
- URLs are plural nouns, kebab where multi-word (`/ai-usage`); path params are `{client_id}`.

## Docstrings & comments
- Every module opens with a short docstring saying what it is and any layer contract
  (e.g. "Repositories flush; this service owns the commit.").
- Comment the **why**, not the what. Match the surrounding comment density — this codebase
  favors a one-line rationale above non-obvious logic, not line-by-line narration.

## Layering idioms (see @CLAUDE.md for the enforceable rules)
- **Routers** parse input, call `ClientService(db).get_client(user, client_id)` for scoping,
  delegate to a service, and set `response_model`. No business logic, no raw queries.
- **Services** own the transaction: mutate, then `self.db.commit()`. Catch integrity errors,
  `rollback()`, and raise a typed error.
- **Repositories** only query/flush, always hard-filtered by `client_id`. Never commit.

## Errors
- Raise typed exceptions from `app/core/exceptions.py` (`NotFoundError`, `AuthError`,
  `ForbiddenError`, `ConflictError`, `TooManyRequestsError`, `ServiceUnavailableError`).
- Never construct `JSONResponse`/`HTTPException` in services — the central handlers own the
  `{"error": {...}}` envelope.
- Avoid bare `except Exception` unless it's a deliberate degradation point (worker loop, audit,
  storage translation) — and comment why.

## Schemas (Pydantic v2)
- Request bodies inherit `StrictModel` (`extra="forbid"`); read models inherit `ORMModel`
  (`from_attributes=True`). Both live in `app/schemas/common.py`.
- Cap every free-text field (`max_length`, using `MAX_TEXT` / `MAX_LONG_LINE`) and every list.
- Use `EmailStr` for emails; `Field(..., ge=, le=)` for numeric bounds.

## Config
- Never read `os.environ` outside `app/core/config`. Add settings to the right
  `app/core/config/*.py` module and document them in `.env.example`.

## Async
- A plain `def` handler is fine (FastAPI threadpools it). In an `async def` handler, never call
  blocking I/O directly — wrap it with `anyio.to_thread.run_sync`.

## Before finishing
Run `make lint` (or `.venv/bin/python -m ruff check .`) and `make format`. The `verify-changes`
skill runs the full gate.
