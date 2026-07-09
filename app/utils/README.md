# app/utils/

Small, generic, dependency-free helpers usable across the app (datetime
formatting, slug generation, pagination helpers).

If a helper needs config, the database, or an external service, it probably
belongs in `services/` or `core/` — not here.
