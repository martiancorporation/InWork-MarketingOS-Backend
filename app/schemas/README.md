# app/schemas/

Pydantic models that define the API's request and response contracts and
validate data at the boundary. One file per domain.

Keep schemas separate from ORM `models/` — the wire format and the storage
format are allowed to differ. Group as `XxxCreate`, `XxxUpdate`, `XxxRead`, etc.
