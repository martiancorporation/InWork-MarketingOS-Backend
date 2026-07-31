"""add dashboard snapshots

Caches the AI dashboard bundle (health score, executive brief, watchdog,
recommendations, QA review) per client, keyed by a hash of the inputs that
produced it. Lets ``GET /clients/{id}/dashboard`` skip the 4-6 AI calls it ran
unconditionally on every request when nothing has actually changed.

Autogenerate also picked up pre-existing drift unrelated to this change
(a stray ``uploads.status`` column/indexes and a knowledge_chunks HNSW index
not reflected in the current models) — deliberately excluded here; only the
new table is created.

Revision ID: 4328bd02dae3
Revises: f4a8c2e9b716
Create Date: 2026-07-31 09:58:30.475087
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4328bd02dae3'
down_revision: str | None = 'f4a8c2e9b716'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'dashboard_snapshots',
        sa.Column('client_id', sa.Uuid(), nullable=False),
        sa.Column(
            'payload',
            sa.JSON(none_as_null=True).with_variant(
                postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql'
            ),
            nullable=False,
        ),
        sa.Column('inputs_hash', sa.String(length=64), nullable=False),
        sa.Column('computed_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['client_id'],
            ['clients.id'],
            name=op.f('fk_dashboard_snapshots_client_id_clients'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_dashboard_snapshots')),
        sa.UniqueConstraint('client_id', name=op.f('uq_dashboard_snapshots_client_id')),
    )


def downgrade() -> None:
    op.drop_table('dashboard_snapshots')
