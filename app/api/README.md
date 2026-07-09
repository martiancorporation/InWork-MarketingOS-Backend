# app/api/

The HTTP layer. Routers are grouped by API version so the contract can evolve
without breaking existing clients.

- `deps.py` — dependencies specific to the API layer (auth guards, pagination params).
- `v1/` — version 1 of the public API.

Routers stay thin: validate input, call a service, return a schema. Business
logic belongs in `app/services/`.
