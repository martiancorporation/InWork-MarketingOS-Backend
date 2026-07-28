"""Plan / task board items for a client."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import TaskCategory, TaskStatus

if TYPE_CHECKING:
    from app.models.client import Client


class PlanTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plan_tasks"
    __table_args__ = (
        Index("ix_plan_tasks_client_status", "client_id", "status"),
        # Backs the calendar's date-range overlap query (a month window).
        Index("ix_plan_tasks_client_dates", "client_id", "start_date", "due_date"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[TaskCategory] = mapped_column(
        pg_enum(TaskCategory, "task_category"), nullable=False, default=TaskCategory.strategy
    )
    status: Mapped[TaskStatus] = mapped_column(
        pg_enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.todo
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # A task spans ``start_date``..``due_date``. Either may be null: a single-day
    # item carries one of them, and an open-ended organic item may carry neither
    # (RD: "If I am doing an organic post it will be forever"). Campaigns are the
    # time-bound case and set both, so the calendar can render a spanning bar.
    start_date: Mapped[date | None] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    # Daily clock window, local to the client's timezone. Plain ``Time`` (not
    # ``timetz``) — SQLite has no timezone-aware time and the offset belongs to
    # the client, not the row.
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="tasks")
