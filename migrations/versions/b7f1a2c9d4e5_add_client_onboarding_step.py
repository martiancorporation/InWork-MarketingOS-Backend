"""add client onboarding_step

Tracks the highest onboarding wizard step a client has completed (1..8), so the
step-by-step onboarding flow can autosave progress and resume a half-finished
wizard. Backfills existing rows to 1.

Revision ID: b7f1a2c9d4e5
Revises: adc6dd263daf
Create Date: 2026-07-09 18:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f1a2c9d4e5"
down_revision: str | None = "adc6dd263daf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "onboarding_step",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    # Drop the server default now that existing rows are backfilled; the app
    # supplies the value going forward (model default / onboarding service).
    op.alter_column("clients", "onboarding_step", server_default=None)


def downgrade() -> None:
    op.drop_column("clients", "onboarding_step")
