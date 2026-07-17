"""Plan / task-board schemas — the internal kanban (todo / in-progress / blocked / done).

Backs the web "Plan" page: a generic, per-client task-management board where the
team creates tasks, assigns them, moves them across statuses, and marks them done.
``PlanTask`` is a flat row (no satellites), so these schemas are deliberately lean.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import TaskCategory, TaskStatus
from app.schemas.common import MAX_TEXT, ORMModel

# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #


class PlanTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=MAX_TEXT)
    category: TaskCategory = TaskCategory.strategy
    status: TaskStatus = TaskStatus.todo
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None


class PlanTaskUpdate(BaseModel):
    """Partial update — only the fields present in the body are applied.

    Presence is detected via ``model_fields_set`` so patching one field (e.g.
    moving a card's status) never clears the others.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=MAX_TEXT)
    category: TaskCategory | None = None
    status: TaskStatus | None = None
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None


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
    due_date: date | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class PlanTaskListResponse(BaseModel):
    items: list[PlanTaskRead]
    total: int
    page: int = 1
    page_size: int = 20
