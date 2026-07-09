# scripts/

Operational and developer convenience scripts that are **not** part of the
running application (startup wrappers, database seeding, one-off maintenance).

Keep each script single-purpose and runnable on its own. Nothing here should be
imported by the app package.

## Scripts
- **`run_local.sh`** — one-command local bootstrap + run. Creates the venv,
  installs deps, writes `.env.local` (with a generated `SECRET_KEY`), starts a
  Docker Postgres, waits for it, runs migrations, and launches the dev server.
  Idempotent — safe to re-run.

  ```bash
  ./scripts/run_local.sh      # or: make start
  ```
- **`start.sh`** — thin startup wrapper placeholder (filled in as needed).
- **`seed_data.py`** — database seeding (placeholder).
