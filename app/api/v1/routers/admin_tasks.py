"""Cross-client task view (v1) — the Admin Panel's "all tasks" list.

- ``GET /admin/tasks`` — admins see every client's tasks; a manager/user sees
  only their assigned clients' tasks (scoped, never a 403 — same stance as
  ``/me/pending``). Filters: client, status, priority, assignee, overdue.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession, Pagination
from app.models.enums import TaskPriority, TaskStatus
from app.schemas.admin_tasks import AdminTaskListResponse
from app.services.admin_task_service import AdminTaskService

router = APIRouter(prefix="/admin/tasks", tags=["admin-tasks"])


@router.get("", response_model=AdminTaskListResponse, summary="Cross-client task list")
def list_tasks(
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    status: TaskStatus | None = Query(None, description="todo / in_progress / blocked / done"),
    priority: TaskPriority | None = Query(None),
    assignee_id: uuid.UUID | None = Query(None),
    client_id: uuid.UUID | None = Query(None),
    overdue_only: bool = Query(False, description="Only tasks past due_date, not done"),
) -> AdminTaskListResponse:
    return AdminTaskService(db).list_tasks(
        user,
        pagination=pagination,
        status=status,
        priority=priority,
        assignee_id=assignee_id,
        client_id=client_id,
        overdue_only=overdue_only,
    )
