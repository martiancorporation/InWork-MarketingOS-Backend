"""Plan / task board items for a client."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, String, Text
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
    __table_args__ = (Index("ix_plan_tasks_client_status", "client_id", "status"),)

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
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="tasks")
