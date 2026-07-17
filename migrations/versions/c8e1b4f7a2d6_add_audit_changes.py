"""add audit_log.changes (before/after diff)

Records the per-field before/after snapshot of a mutated record on the audit
row, so the log answers "who changed this value, and from what" — a
field-level accountability trail.

Revision ID: c8e1b4f7a2d6
Revises: bf3c2a1d9e04
Create Date: 2026-07-16 14:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8e1b4f7a2d6"
down_revision: str | None = "bf3c2a1d9e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column(
            "changes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("audit_log", "changes")
