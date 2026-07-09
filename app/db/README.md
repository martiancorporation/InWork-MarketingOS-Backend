# app/db/

Database engine, session lifecycle, and the declarative base.

- `base.py` — the SQLAlchemy declarative `Base` (with a metadata naming convention).
- `types.py` — shared column types (`GUID`, `TZDateTime`) and the `pg_enum()` helper.
- `mixins.py` — reusable model mixins (`UUIDPrimaryKeyMixin`, `CreatedAtMixin`, `TimestampMixin`).
- `session.py` — engine + session factory and the `get_db` request-scoped dependency.

No table definitions here — those live in `app/models/`. Models import their
building blocks from `app/models/base.py`, which re-exports everything above.
