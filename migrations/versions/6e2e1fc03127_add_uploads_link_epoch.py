"""add uploads.link_epoch

Lets a specific upload's signed permalink (app/utils/download_link.py) be
revoked without deleting the file: bumping ``link_epoch`` invalidates every
previously-issued signature for that upload, since the epoch is folded into
the HMAC input.

Revision ID: 6e2e1fc03127
Revises: e239a1c65481
Create Date: 2026-07-31 14:45:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6e2e1fc03127'
down_revision: str | None = 'e239a1c65481'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'uploads',
        sa.Column('link_epoch', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('uploads', 'link_epoch', server_default=None)


def downgrade() -> None:
    op.drop_column('uploads', 'link_epoch')
