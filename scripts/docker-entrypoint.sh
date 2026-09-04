#!/usr/bin/env sh
# Container entrypoint: optionally apply DB migrations, then exec the server.
#
# Set RUN_MIGRATIONS=1 to run `alembic upgrade head` before starting (safe to
# leave off when migrations are applied by a separate deploy job). Migrations
# run behind a Postgres advisory lock (scripts/migrate_with_lock.py) so
# multiple replicas starting at once don't race each other.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Applying database migrations…"
  python scripts/migrate_with_lock.py
fi

exec "$@"
