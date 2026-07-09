# app/core/

Cross-cutting concerns used everywhere in the app. Nothing here is tied to a
single feature.

- `config/` — typed settings loaded from the environment (the ONLY place secrets enter the app).
- `security.py` — password hashing & token helpers.
- `logging.py` — logging setup.
- `exceptions.py` — custom exception types + FastAPI error handlers.
- `middleware.py` — app-wide middleware registration.
- `dependencies.py` — shared FastAPI dependencies (e.g. current user, DB session).
- `constants.py` — fixed, non-secret constants (enums, defaults).

Rule: never hardcode a secret or environment value here — read it via `config`.
