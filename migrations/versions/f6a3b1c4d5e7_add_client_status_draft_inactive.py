"""add client status values 'draft' and 'inactive' + backfill

Extends the ``client_status`` enum with ``draft`` (client still being set up in
the onboarding wizard) and ``inactive`` (paused / switched off). Backfills
existing rows so nothing stays stuck at the legacy ``onboarding`` value:

* ``onboarding`` with a finished wizard (onboarding_step >= 8) → ``active``
* ``onboarding`` still in progress (onboarding_step < 8)        → ``draft``
* ``paused``                                                    → ``inactive``

Postgres can't add enum values inside a transaction, so the ADD VALUE runs in an
autocommit block; the backfill runs in the normal migration transaction after.
On non-Postgres backends (SQLite tests) the column is plain text, so only the
backfill applies.

Revision ID: f6a3b1c4d5e7
Revises: e5b2c9f18a47
Create Date: 2026-07-15 11:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a3b1c4d5e7"
down_revision: str | None = "e5b2c9f18a47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # New enum values must be committed before they can be used in the
        # backfill UPDATE below, so add them in their own autocommit block.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE client_status ADD VALUE IF NOT EXISTS 'draft'")
            op.execute("ALTER TYPE client_status ADD VALUE IF NOT EXISTS 'inactive'")

    op.execute(
        "UPDATE clients SET status = 'active' "
        "WHERE status = 'onboarding' AND onboarding_step >= 8"
    )
    op.execute(
        "UPDATE clients SET status = 'draft' "
        "WHERE status = 'onboarding' AND onboarding_step < 8"
    )
    op.execute("UPDATE clients SET status = 'inactive' WHERE status = 'paused'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type, so we only revert the data
    # (the extra enum values remain but are harmless).
    op.execute("UPDATE clients SET status = 'onboarding' WHERE status = 'draft'")
    op.execute("UPDATE clients SET status = 'paused' WHERE status = 'inactive'")
