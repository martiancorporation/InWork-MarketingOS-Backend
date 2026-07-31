"""Plan / task-board schemas — the internal kanban (todo / in-progress / blocked / done).

Backs the web "Plan" page: a generic, per-client task-management board where the
team creates tasks, assigns them, moves them across statuses, and marks them done.
``PlanTask`` is a flat row (no satellites), so these schemas are deliberately lean.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.models.enums import TaskCategory, TaskStatus
from app.schemas.common import MAX_TEXT, ORMModel, StrictModel


def _check_ranges(start: date | None, end: date | None, t0: time | None, t1: time | None) -> None:
    """Reject inverted ranges instead of quietly storing them.

    The web client swaps reversed dates before sending, but the API is also a
    public surface — a caller that posts ``start_date`` after ``due_date`` gets a
    422 rather than a task whose span renders backwards (or not at all).
    """
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must be on or before due_date")
    if t0 is not None and t1 is not None and t0 > t1:
        raise ValueError("start_time must be on or before end_time")


# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #


class PlanTaskCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=MAX_TEXT)
    category: TaskCategory = TaskCategory.strategy
    status: TaskStatus = TaskStatus.todo
    assignee_id: uuid.UUID | None = None
    #: A task spans ``start_date``..``due_date``; either may be omitted for a
    #: single-day item, and both for an open-ended organic one.
    start_date: date | None = None
    due_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None

    @model_validator(mode="after")
    def _validate_ranges(self) -> Self:
        _check_ranges(self.start_date, self.due_date, self.start_time, self.end_time)
        return self


class PlanTaskUpdate(StrictModel):
    """Partial update — only the fields present in the body are applied.

    Presence is detected via ``model_fields_set`` so patching one field (e.g.
    moving a card's status) never clears the others.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=MAX_TEXT)
    category: TaskCategory | None = None
    status: TaskStatus | None = None
    assignee_id: uuid.UUID | None = None
    start_date: date | None = None
    due_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None

    @model_validator(mode="after")
    def _validate_ranges(self) -> Self:
        # Only meaningful when both ends of a range arrive together; a partial
        # patch that moves one edge is checked against the stored row in the
        # service, where the other edge is known.
        _check_ranges(self.start_date, self.due_date, self.start_time, self.end_time)
        return self


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


class PlanTaskRead(ORMModel):
    """A task-board card."""

    id: uuid.UUID
    client_id: uuid.UUID
    title: str
    description: str | None = None
    category: TaskCategory
    status: TaskStatus
    assignee_id: uuid.UUID | None = None
    start_date: date | None = None
    due_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class PlanTaskListResponse(BaseModel):
    items: list[PlanTaskRead]
    total: int
    page: int = 1
    page_size: int = 20
