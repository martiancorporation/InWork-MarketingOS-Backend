# app/core/

Cross-cutting concerns used everywhere in the app. Nothing here is tied to a
single feature.

- `config/` — typed settings loaded from the environment (the ONLY place secrets enter the app).
- `security.py` — password hashing & token helpers.
- `logging.py` — logging setup.
- `exceptions.py` — custom exception types + FastAPI error handlers.
- `middleware.py` — app-wide middleware (audit trail, request body cap, security headers).
- `request_context.py` — per-request context (request id, audit change set).
- `rate_limit.py` — sliding-window limiter (in-memory or Redis-backed).
- `pagination.py` — the shared `Pagination` dependency for list endpoints.

Rule: never hardcode a secret or environment value here — read it via `config`.
