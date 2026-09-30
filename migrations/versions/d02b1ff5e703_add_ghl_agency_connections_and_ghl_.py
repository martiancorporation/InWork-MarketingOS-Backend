"""add ghl_agency_connections table and ghl SocialPlatform enum value

Revision ID: d02b1ff5e703
Revises: 4789fa2ab09d
Create Date: 2026-09-30 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd02b1ff5e703'
down_revision: str | None = '4789fa2ab09d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `integration_status` already exists (created by the original `integrations`
# table's migration) — declared with create_type=False so this table's
# creation never emits CREATE TYPE, which would fail with DuplicateObject.
integration_status = postgresql.ENUM(
    'connected', 'disconnected', 'error', 'pending', 'needs_reauth',
    name='integration_status',
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        'ghl_agency_connections',
        sa.Column(
            'status',
            integration_status,
            nullable=False,
        ),
        sa.Column('company_id', sa.String(length=80), nullable=True),
        sa.Column('location_id', sa.String(length=80), nullable=True),
        sa.Column('access_token_encrypted', sa.Text(), nullable=True),
        sa.Column('refresh_token_encrypted', sa.Text(), nullable=True),
        sa.Column('token_expires_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('connected_by', sa.Uuid(), nullable=True),
        sa.Column('last_sync_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['connected_by'], ['users.id'],
            name=op.f('fk_ghl_agency_connections_connected_by_users'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ghl_agency_connections')),
    )
    # Native Postgres enum — a new value needs an explicit ALTER TYPE (a model
    # change alone doesn't migrate an existing enum type). Must run outside
    # any open transaction block that later touches the same enum in the same
    # migration (Alembic runs each op autocommit-style here, so this is safe
    # on its own).
    op.execute("ALTER TYPE social_platform ADD VALUE IF NOT EXISTS 'ghl'")


def downgrade() -> None:
    # Postgres cannot drop a single enum value, so the 'ghl' member of
    # social_platform is left in place on downgrade (harmless — matches how
    # this repo already treats deprecated SocialPlatform members, see
    # app/models/enums.py's docstring on x/pinterest/email).
    op.drop_table('ghl_agency_connections')
