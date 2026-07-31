"""add intel_jobs running/locked_at index

``JobQueue.reclaim_stale`` scans for jobs with ``status='running'`` and a stale
``locked_at`` to requeue/dead-letter them. The existing ``ix_intel_jobs_claim``
index is on ``(status, run_after)`` and doesn't serve that query — add a
composite index on ``(status, locked_at)``.

Revision ID: e239a1c65481
Revises: 4328bd02dae3
Create Date: 2026-07-31 14:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e239a1c65481'
down_revision: str | None = '4328bd02dae3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_intel_jobs_running_locked_at',
        'intel_jobs',
        ['status', 'locked_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_intel_jobs_running_locked_at', table_name='intel_jobs')
