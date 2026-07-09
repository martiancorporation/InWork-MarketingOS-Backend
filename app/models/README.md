# app/models/

SQLAlchemy ORM models — the database entities. One file per entity (or closely
related group) to keep files small.

Models describe *shape and relationships only*. No business logic, no queries
(those go in `repositories/`), no request/response shaping (that is `schemas/`).
Detailed schema design is added during implementation.
