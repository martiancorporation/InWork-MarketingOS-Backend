"""add support tickets

Support & feedback ticket system: a global, owner-scoped resource (not
client-scoped) — ``support_tickets`` plus its attachment join table
(referencing the existing global ``uploads`` table directly) and its reply
thread table.

Revision ID: 4f1d9a2c2f43
Revises: 6e2e1fc03127
Create Date: 2026-08-01 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4f1d9a2c2f43'
down_revision: str | None = '6e2e1fc03127'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'support_tickets',
        sa.Column('ticket_number', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column(
            'category',
            sa.Enum(
                'bug', 'feature_request', 'billing', 'account', 'technical_support',
                'feedback', 'other',
                name='ticket_category',
            ),
            nullable=False,
        ),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column(
            'priority',
            sa.Enum('low', 'medium', 'high', 'urgent', name='ticket_priority'),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum('open', 'in_progress', 'resolved', 'closed', name='ticket_status'),
            nullable=False,
        ),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name=op.f('fk_support_tickets_created_by_users'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_support_tickets')),
        sa.UniqueConstraint('ticket_number', name=op.f('uq_support_tickets_ticket_number')),
    )
    op.create_index(
        op.f('ix_support_tickets_category'), 'support_tickets', ['category'],
    )
    op.create_index(
        op.f('ix_support_tickets_created_by'), 'support_tickets', ['created_by'],
    )
    op.create_index(
        op.f('ix_support_tickets_priority'), 'support_tickets', ['priority'],
    )
    op.create_index(
        op.f('ix_support_tickets_status'), 'support_tickets', ['status'],
    )
    op.create_index(
        'ix_support_tickets_owner_status', 'support_tickets', ['created_by', 'status'],
    )
    op.create_index(
        'ix_support_tickets_status_priority', 'support_tickets', ['status', 'priority'],
    )

    op.create_table(
        'support_ticket_attachments',
        sa.Column('ticket_id', sa.Uuid(), nullable=False),
        sa.Column('upload_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ['ticket_id'], ['support_tickets.id'],
            name=op.f('fk_support_ticket_attachments_ticket_id_support_tickets'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['upload_id'], ['uploads.id'],
            name=op.f('fk_support_ticket_attachments_upload_id_uploads'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_support_ticket_attachments')),
    )
    op.create_index(
        op.f('ix_support_ticket_attachments_ticket_id'),
        'support_ticket_attachments', ['ticket_id'],
    )

    op.create_table(
        'support_ticket_replies',
        sa.Column('ticket_id', sa.Uuid(), nullable=False),
        sa.Column('author_id', sa.Uuid(), nullable=True),
        sa.Column('author_role', sa.String(length=16), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['author_id'], ['users.id'],
            name=op.f('fk_support_ticket_replies_author_id_users'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['ticket_id'], ['support_tickets.id'],
            name=op.f('fk_support_ticket_replies_ticket_id_support_tickets'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_support_ticket_replies')),
    )
    op.create_index(
        op.f('ix_support_ticket_replies_ticket_id'),
        'support_ticket_replies', ['ticket_id'],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_support_ticket_replies_ticket_id'), table_name='support_ticket_replies')
    op.drop_table('support_ticket_replies')

    op.drop_index(
        op.f('ix_support_ticket_attachments_ticket_id'), table_name='support_ticket_attachments'
    )
    op.drop_table('support_ticket_attachments')

    op.drop_index('ix_support_tickets_status_priority', table_name='support_tickets')
    op.drop_index('ix_support_tickets_owner_status', table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_status'), table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_priority'), table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_created_by'), table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_category'), table_name='support_tickets')
    op.drop_table('support_tickets')
