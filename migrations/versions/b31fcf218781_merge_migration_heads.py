"""merge migration heads

Revision ID: b31fcf218781
Revises: e1a2b3c4d5f6, e8d2b3c4e5f6
Create Date: 2026-07-20 11:23:05.857566
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b31fcf218781'
down_revision: str | None = ('e1a2b3c4d5f6', 'e8d2b3c4e5f6')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
