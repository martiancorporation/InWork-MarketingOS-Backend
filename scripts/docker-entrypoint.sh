#!/usr/bin/env sh
# Container entrypoint: optionally apply DB migrations, then exec the server.
#
# Set RUN_MIGRATIONS=1 to run `alembic upgrade head` before starting (safe to
# leave off when migrations are applied by a separate deploy job).
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Applying database migrations…"
  alembic upgrade head
fi

exec "$@"
