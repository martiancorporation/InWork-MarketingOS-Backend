"""add task priority, plan task event link, event post content format

Revision ID: 1dd120a4fea0
Revises: e7f0c061955f
Create Date: 2026-09-14 10:02:04.507720
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1dd120a4fea0'
down_revision: str | None = 'e7f0c061955f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


task_priority_enum = sa.Enum('low', 'medium', 'high', 'urgent', name='task_priority')


def upgrade() -> None:
    op.add_column('event_posts', sa.Column('content_format', sa.String(length=30), nullable=True))
    # A new native Postgres enum used in an ADD COLUMN (not CREATE TABLE) isn't
    # auto-created by the DDL compiler — create it explicitly first.
    task_priority_enum.create(op.get_bind(), checkfirst=True)
    # server_default backfills existing rows to "medium"; dropped right after so
    # the app (model default / PlanGenerationService) supplies it going forward
    # — same pattern as b7f1a2c9d4e5_add_client_onboarding_step.
    op.add_column(
        'plan_tasks',
        sa.Column('priority', task_priority_enum, nullable=False, server_default='medium'),
    )
    op.alter_column('plan_tasks', 'priority', server_default=None)
    op.add_column('plan_tasks', sa.Column('event_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_plan_tasks_event_id'), 'plan_tasks', ['event_id'], unique=False)
    op.create_foreign_key(op.f('fk_plan_tasks_event_id_marketing_events'), 'plan_tasks', 'marketing_events', ['event_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_plan_tasks_event_id_marketing_events'), 'plan_tasks', type_='foreignkey')
    op.drop_index(op.f('ix_plan_tasks_event_id'), table_name='plan_tasks')
    op.drop_column('plan_tasks', 'event_id')
    op.drop_column('plan_tasks', 'priority')
    task_priority_enum.drop(op.get_bind(), checkfirst=True)
    op.drop_column('event_posts', 'content_format')
