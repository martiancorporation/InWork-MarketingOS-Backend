# migrations/

Alembic database migrations. Each schema change is a versioned, reviewable file
under `versions/`. `env.py` wires Alembic to the app's database configuration.

Do not edit generated migration files after they have been applied to a shared
environment — create a new migration instead.
