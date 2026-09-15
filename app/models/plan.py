"""Plan / task board items for a client."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import TaskCategory, TaskPriority, TaskStatus

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.event import MarketingEvent


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
    # Distinct from `description` per the client's spec ("description AND
    # requirements") — description is what the post/deliverable IS, this is
    # what the assignee must satisfy to consider it done (constraints, specs,
    # approval criteria). Free text; not every task needs one.
    requirements: Mapped[str | None] = mapped_column(Text)
    category: Mapped[TaskCategory] = mapped_column(
        pg_enum(TaskCategory, "task_category"), nullable=False, default=TaskCategory.strategy
    )
    status: Mapped[TaskStatus] = mapped_column(
        pg_enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.todo
    )
    priority: Mapped[TaskPriority] = mapped_column(
        pg_enum(TaskPriority, "task_priority"), nullable=False, default=TaskPriority.medium
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Set only by PlanGenerationService — bridges an AI-generated content task to
    # its calendar item (script/hashtags/platform/approval workflow live there).
    # Null for every non-content task (SEO audit, dev work, campaign setup, …).
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("marketing_events.id", ondelete="SET NULL"), index=True
    )
    # A task spans ``start_date``..``due_date``. Either may be null: a single-day
    # item carries one of them, and an open-ended organic post — one with no
    # planned end — may carry neither. Campaigns are the time-bound case and
    # set both, so the calendar can render a spanning bar.
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
    # Soft-hide from the board/admin list without touching `status` (which
    # stays exactly todo/in_progress/blocked/done — see the deferred status-
    # enum rework). A deliberate boolean flag, not a fifth status value.
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    client: Mapped[Client] = relationship(back_populates="tasks")
    event: Mapped[MarketingEvent | None] = relationship()
    assets: Mapped[list[PlanTaskAsset]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="PlanTaskAsset.position"
    )
    notes: Mapped[list[PlanTaskNote]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="PlanTaskNote.created_at"
    )


class PlanTaskAsset(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A supporting link for a task — the client's "supporting links or
    attachments" requirement. Deliberately a plain URL (Drive/Figma/brief
    doc/etc.), not a real file-upload pipeline like ``EventAsset``/
    ``Document`` — that's a materially bigger feature (storage, virus
    scanning, an upload widget) that nothing here asked for; a link covers
    the common case with no new infrastructure.
    """

    __tablename__ = "plan_task_assets"

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("plan_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    task: Mapped[PlanTask] = relationship(back_populates="assets")


class PlanTaskNote(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A comment on a task — the "add notes/comments" context-menu action.
    Plain append-only log, no edit/delete (matches a comment thread, not a
    document); the audit trail for field changes stays on ``audit_log``."""

    __tablename__ = "plan_task_notes"

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("plan_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    task: Mapped[PlanTask] = relationship(back_populates="notes")
