# app/repositories/

The data-access layer. Each repository encapsulates all database reads/writes
for one entity, so queries live in exactly one place.

- `base.py` — a generic repository with common CRUD helpers.
- `xxx_repository.py` — per-entity queries.

Repositories return models/data — they contain no business rules (that is
`services/`).
