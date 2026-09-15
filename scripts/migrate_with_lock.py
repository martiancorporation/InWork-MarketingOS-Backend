"""Run `alembic upgrade head` behind a Postgres advisory lock.

Guards against a rolling deploy or horizontal scale-up starting several
container replicas at once: without this, every replica would run `alembic
upgrade head` concurrently on startup, which is safe for ordinary transactional
DDL but not for anything Alembic might be asked to emit outside a transaction
(e.g. `CREATE INDEX CONCURRENTLY`). The first replica to acquire the lock runs
migrations; the rest block until it releases, then find nothing left to do.

Run from the Backend/ directory with the virtualenv active:
    python scripts/migrate_with_lock.py     # or via docker-entrypoint.sh
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import get_engine  # noqa: E402

# Arbitrary fixed key shared by every replica of this app — any int64 works, it
# just has to be the same constant everywhere so replicas contend on one lock.
_MIGRATION_LOCK_KEY = 727_501_001


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        print("Waiting for the migration advisory lock…")
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
        try:
            print("Lock acquired — applying database migrations…")
            subprocess.run(["alembic", "upgrade", "head"], check=True)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY})
            conn.commit()


if __name__ == "__main__":
    main()
