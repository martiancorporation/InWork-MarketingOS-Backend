---
name: add-migration
description: >-
  Creates and reviews an Alembic database migration for this backend safely —
  autogenerate, verify against the models, require a real downgrade, and handle
  enums, pgvector, and foreign keys correctly. Use when changing a SQLAlchemy
  model, adding a table/column, or writing/reviewing anything in
  migrations/versions/.
allowed-tools: Read, Grep, Glob, Edit, Bash
---

# Add / review a migration

Migrations are Alembic, in `migrations/versions/`, with a deterministic naming convention
(see `app/db/base.py`). Models are the source of truth; the migration must match them.

## Workflow
1. **Change the model first** in `app/models/`, and register it in `app/models/__init__.py`
   if it's new (autogenerate only sees imported models).
2. **Autogenerate:**
   ```
   make migration m="add <thing>"      # alembic revision --autogenerate
   ```
3. **Review the generated file — always.** Autogenerate is a draft, not gospel.

## Review checklist
- [ ] **`downgrade()` is real** — it actually reverses `upgrade()` (drop what you created,
      restore what you changed). Never leave it empty or `pass`.
- [ ] **Foreign keys** use the intended `ondelete` (client-scoped rows are usually
      `ondelete="CASCADE"`) and are indexed.
- [ ] **Indexes** on foreign keys and columns you filter/sort by are present.
- [ ] **Enums:** this repo uses native Postgres enums that store the enum *value*
      (`pg_enum(...)`, `values_callable`). Adding a value to an existing enum needs an explicit
      `ALTER TYPE ... ADD VALUE` (see `f6a3b1c4d5e7_add_client_status_draft_inactive.py`) — a
      model edit alone won't migrate it.
- [ ] **pgvector:** vector columns require the extension. The intelligence migration runs
      `op.execute("CREATE EXTENSION IF NOT EXISTS vector")`; the DB image must ship it
      (`pgvector/pgvector:pg16` in CI + docker-compose).
- [ ] **No data loss** on downgrade paths you didn't intend (dropping a column drops its data).
- [ ] **Naming convention** matches `app/db/base.py` (ix_/uq_/fk_/pk_/ck_ prefixes).
- [ ] `down_revision` points at the correct previous head; there's a single head
      (`alembic heads` shows one).

## Verify it applies both ways
Against a real Postgres (SQLite won't exercise enums/pgvector):
```
make db-up                 # pgvector/pgvector:pg16
make migrate               # alembic upgrade head
make downgrade             # alembic downgrade -1  (confirm it reverses cleanly)
make migrate               # re-apply
```
CI also runs `alembic upgrade head` against Postgres — keep the model/migration in sync or
that job fails on drift.

## Gotchas
- Autogenerate can miss `server_default` changes, enum value additions, and index renames —
  add those by hand.
- Never edit a migration that has already been applied in a shared environment; add a new one.
