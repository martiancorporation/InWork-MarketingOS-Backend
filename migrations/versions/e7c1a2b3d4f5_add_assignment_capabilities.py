"""add per-project capabilities to client_assignments

Granular per-project RBAC (BE-03): a client assignment can now grant a subset of
``ClientCapability`` values (JSON list). ``NULL`` means "full set" so every
pre-existing assignment keeps working unchanged — nothing to backfill.

Stored as JSON (JSONB on Postgres) rather than a native enum: the capability set
is app-defined and grows with new features, so an enum type would force a
migration per addition (the same rationale as ``client_platforms.channel``).

Revision ID: e7c1a2b3d4f5
Revises: d4f9a1c7e2b5
Create Date: 2026-07-20 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7c1a2b3d4f5"
down_revision: str | None = "d4f9a1c7e2b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "client_assignments",
        sa.Column(
            "capabilities",
            sa.JSON(none_as_null=True).with_variant(
                postgresql.JSONB(none_as_null=True), "postgresql"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("client_assignments", "capabilities")
