"""add analytics_daily.source (row provenance)

Tags where a daily fact came from — ``synthetic`` for seeded demo data,
``connector`` for a real integration sync, ``csv`` for an operator import.

Deliberately OUTSIDE the ``(client_id, date, platform)`` natural key: a real
sync must overwrite a synthetic cell in place rather than create a second row
for the same day.

Revision ID: d9e4a1b7c250
Revises: b31fcf218781
Create Date: 2026-07-27 18:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e4a1b7c250"
down_revision: str | None = "b31fcf218781"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analytics_daily", sa.Column("source", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("analytics_daily", "source")
