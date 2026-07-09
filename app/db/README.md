# app/db/

Database engine, session lifecycle, and the declarative base.

- `base.py` — the SQLAlchemy declarative base every model inherits from.
- `session.py` — engine + session factory and the request-scoped session dependency.
- `mixins.py` — reusable model mixins (timestamps, UUID primary keys, etc.).

No table definitions here — those live in `app/models/`.
