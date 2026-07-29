"""add csv to report_format

The report builder UI (`DOWNLOAD_FORMATS` in the frontend) has always offered a
CSV download option, but the backend's `report_format` enum only ever defined
`pdf`, `excel`, `visual` — picking CSV would fail schema validation. This adds
the missing value so all four formats the UI advertises are actually valid.

Postgres can't add enum values inside a transaction, so the ADD VALUE runs in an
autocommit block. On non-Postgres backends (SQLite tests) the column is plain
text, so this migration is a no-op there.

Revision ID: f4a8c2e9b716
Revises: c4f8e2a91d63
Create Date: 2026-07-29 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a8c2e9b716"
down_revision: str | None = "c4f8e2a91d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite/other: enum column is plain text — nothing to alter.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE report_format ADD VALUE IF NOT EXISTS 'csv'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type; the added value is
    # harmless if left in place, so the downgrade is intentionally a no-op.
    pass
