"""add ai_usage_events

Dedicated, immutable per-request AI usage log: attribution (user/client/feature),
provider/model/operation, token counts (input/output/cache), and USD cost
snapshotted at call time.

Revision ID: c3e8f1a04b62
Revises: b7f1a2c9d4e5
Create Date: 2026-07-10 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3e8f1a04b62"
down_revision: str | None = "b7f1a2c9d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_events",
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("client_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("input_cost", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("output_cost", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("cache_cost", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("total_cost", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("priced", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=80), nullable=True),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name=op.f("fk_ai_usage_events_actor_user_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"],
            name=op.f("fk_ai_usage_events_client_id_clients"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_usage_events")),
    )
    op.create_index(op.f("ix_ai_usage_events_actor_user_id"), "ai_usage_events", ["actor_user_id"])
    op.create_index(op.f("ix_ai_usage_events_client_id"), "ai_usage_events", ["client_id"])
    op.create_index(op.f("ix_ai_usage_events_total_cost"), "ai_usage_events", ["total_cost"])
    op.create_index("ix_ai_usage_client_created", "ai_usage_events", ["client_id", "created_at"])
    op.create_index("ix_ai_usage_actor_created", "ai_usage_events", ["actor_user_id", "created_at"])
    op.create_index("ix_ai_usage_feature", "ai_usage_events", ["feature"])
    op.create_index("ix_ai_usage_model", "ai_usage_events", ["model"])
    op.create_index("ix_ai_usage_created_at", "ai_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_created_at", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_model", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_feature", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_actor_created", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_client_created", table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_total_cost"), table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_client_id"), table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_actor_user_id"), table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
