"""make ai_model_routes.model_id nullable

Revision ID: 4789fa2ab09d
Revises: 218100ec838d
Create Date: 2026-09-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4789fa2ab09d"
down_revision: str | None = "ce1337c8e316"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A category can now legitimately have no admin-configured model yet (the
    # dynamic-catalog refactor removed the hardcoded per-category default
    # that used to backfill this column) — see app/ai/model_router.py.
    op.alter_column(
        "ai_model_routes", "model_id", existing_type=sa.String(length=80), nullable=True
    )


def downgrade() -> None:
    # Re-imposing NOT NULL would fail if any row is genuinely unconfigured —
    # backfill with an explicit placeholder (never a real model id) first so
    # the rollback itself doesn't silently invent a routing decision.
    op.execute("UPDATE ai_model_routes SET model_id = 'unconfigured' WHERE model_id IS NULL")
    op.alter_column(
        "ai_model_routes", "model_id", existing_type=sa.String(length=80), nullable=False
    )
