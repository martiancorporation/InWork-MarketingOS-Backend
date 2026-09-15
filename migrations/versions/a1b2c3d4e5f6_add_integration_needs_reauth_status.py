"""add 'needs_reauth' to the integration_status enum

Adds a new ``needs_reauth`` value to the native Postgres ``integration_status``
enum, distinct from the existing ``error`` value: it means a sync failed for a
reason that specifically indicates the OAuth grant itself is dead (revoked
token, ``invalid_grant``, HTTP 401) rather than a transient/network failure
worth simply retrying. See ``IntegrationService.sync`` for where it's set.

Postgres can't add an enum value inside a transaction, so it runs in its own
autocommit block, guarded to Postgres only (SQLite, used in tests, stores the
column as plain text — no migration needed there). No backfill: nothing writes
this value until the accompanying service-layer change ships, so there are no
existing rows to update. Downgrade is a no-op — Postgres cannot drop a value
from an enum type; the extra value remains but is harmless.

Revision ID: a1b2c3d4e5f6
Revises: 5c79164e1108
Create Date: 2026-09-02 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "5c79164e1108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE integration_status ADD VALUE IF NOT EXISTS 'needs_reauth'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type. Nothing to revert.
    pass
