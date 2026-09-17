"""add ghl integration key and tags

Adds 'ghl' to the integration_key enum (GoHighLevel/LeadConnector — one
shared location across clients, distinguished by contact/opportunity tags)
and a new ``integrations.ghl_tags`` column storing each client's own tags
under that shared location.

Postgres can't add an enum value inside a transaction, so it runs in its own
autocommit block, same as f6a3b1c4d5e7.

Revision ID: 53d83c86a848
Revises: f824b63a3767
Create Date: 2026-09-15 14:52:09.396543
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '53d83c86a848'
down_revision: str | None = 'f824b63a3767'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE integration_key ADD VALUE IF NOT EXISTS 'ghl'")
    op.add_column('integrations', sa.Column('ghl_tags', sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql'), nullable=True))


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type — the extra 'ghl' value
    # remains but is harmless (mirrors f6a3b1c4d5e7's downgrade).
    op.drop_column('integrations', 'ghl_tags')
