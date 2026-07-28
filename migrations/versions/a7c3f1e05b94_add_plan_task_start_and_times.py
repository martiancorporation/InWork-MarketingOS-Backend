"""add plan_tasks start_date / start_time / end_time (multi-day planning)

A plan item was a single ``due_date``, so a campaign could not express the
"runs 1–31 July" shape the calendar needs to draw a spanning bar. The web Plan
page already sent ``start_date``/``start_time``/``end_time`` and already rendered
spanning bars, but ``PlanTaskCreate`` was a plain ``BaseModel`` (Pydantic's
default is to *ignore* unknown keys), so those three fields were silently
dropped and the API still answered 201 — multi-day planning looked like it
worked until the page was reloaded.

All three columns are nullable, which is what keeps them backwards-compatible:
existing rows and single-day items simply leave them empty, and an open-ended
organic item may carry no dates at all.

``Time`` rather than ``timetz``: the offset belongs to the client (see
``clients.timezone``), not to each row, and SQLite — which the test suite runs
on — has no timezone-aware time type.

The composite index backs the calendar's month-window overlap query
(``coalesce(start_date, due_date) <= :end AND coalesce(due_date, start_date) >= :start``),
mirroring the existing ``ix_plan_tasks_client_status``.

Revision ID: a7c3f1e05b94
Revises: d9e4a1b7c250
Create Date: 2026-07-28 10:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3f1e05b94"
down_revision: str | None = "d9e4a1b7c250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plan_tasks", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("plan_tasks", sa.Column("start_time", sa.Time(), nullable=True))
    op.add_column("plan_tasks", sa.Column("end_time", sa.Time(), nullable=True))
    op.create_index("ix_plan_tasks_start_date", "plan_tasks", ["start_date"])
    op.create_index(
        "ix_plan_tasks_client_dates", "plan_tasks", ["client_id", "start_date", "due_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_plan_tasks_client_dates", table_name="plan_tasks")
    op.drop_index("ix_plan_tasks_start_date", table_name="plan_tasks")
    op.drop_column("plan_tasks", "end_time")
    op.drop_column("plan_tasks", "start_time")
    op.drop_column("plan_tasks", "start_date")
