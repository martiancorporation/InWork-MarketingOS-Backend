"""add strategies table

Strategy-adherence tracking (BE-06): records the AI-given strategy the operator
signed off on, as immutable per-client versions. The current strategy is the
highest ``version`` for a client; adherence is computed at read time from
recommendation decisions and plan-task completion (no stored score).

Revision ID: e8d2b3c4e5f6
Revises: e7c1a2b3d4f5
Create Date: 2026-07-20 10:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8d2b3c4e5f6"
down_revision: str | None = "e7c1a2b3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("signed_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_strategies_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signed_by"],
            ["users.id"],
            name=op.f("fk_strategies_signed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategies")),
    )
    op.create_index(
        op.f("ix_strategies_client_id"), "strategies", ["client_id"], unique=False
    )
    op.create_index(
        "ix_strategies_client_version",
        "strategies",
        ["client_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_strategies_client_version", table_name="strategies")
    op.drop_index(op.f("ix_strategies_client_id"), table_name="strategies")
    op.drop_table("strategies")
