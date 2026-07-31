"""Plan / task-board API (v1) — the internal kanban (todo / in-progress / blocked / done).

- ``GET    /clients/{id}/plan/tasks``            — list (board columns / filters / date window)
- ``POST   /clients/{id}/plan/tasks``            — create a task
- ``GET    /clients/{id}/plan/tasks/{task_id}``  — task detail
- ``PATCH  /clients/{id}/plan/tasks/{task_id}``  — partial edit (move status / reassign)
- ``DELETE /clients/{id}/plan/tasks/{task_id}``  — remove

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404, never revealing its
existence. Any user who can see the client may manage its task board.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequireClient
from app.models.enums import TaskCategory, TaskStatus
from app.schemas.common import MessageResponse
from app.schemas.plan import (
    PlanTaskCreate,
    PlanTaskListResponse,
    PlanTaskRead,
    PlanTaskUpdate,
)
from app.services.plan_service import PlanService

router = APIRouter(prefix="/clients/{client_id}/plan", tags=["plan"])


@router.get("/tasks", response_model=PlanTaskListResponse, summary="List plan tasks")
def list_tasks(
    client_id: uuid.UUID,
    db: DbSession,
    pagination: Pagination,
    _client: RequireClient,
    status: TaskStatus | None = Query(None, description="todo / in_progress / blocked / done"),
    category: TaskCategory | None = Query(None),
    assignee_id: uuid.UUID | None = Query(None),
    start: date | None = Query(None, description="Window start (inclusive), YYYY-MM-DD"),
    end: date | None = Query(None, description="Window end (inclusive), YYYY-MM-DD"),
    include_undated: bool = Query(
        False, description="With a window, also return tasks that have no dates at all"
    ),
) -> PlanTaskListResponse:
    return PlanService(db).list_tasks(
        client_id,
        pagination=pagination,
        status=status,
        category=category,
        assignee_id=assignee_id,
        start=start,
        end=end,
        include_undated=include_undated,
    )


@router.post(
    "/tasks",
    response_model=PlanTaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a plan task",
)
def create_task(
    client_id: uuid.UUID,
    data: PlanTaskCreate,
    user: CurrentUser,
    db: DbSession,
    _client: RequireClient,
) -> PlanTaskRead:
    task = PlanService(db).create_task(client_id, data, created_by=user.id)
    return PlanTaskRead.model_validate(task)


@router.get("/tasks/{task_id}", response_model=PlanTaskRead, summary="Get a plan task")
def get_task(
    client_id: uuid.UUID, task_id: uuid.UUID, db: DbSession, _client: RequireClient
) -> PlanTaskRead:
    task = PlanService(db).get_task(client_id, task_id)
    return PlanTaskRead.model_validate(task)


@router.patch("/tasks/{task_id}", response_model=PlanTaskRead, summary="Edit / move a plan task")
def update_task(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    data: PlanTaskUpdate,
    db: DbSession,
    _client: RequireClient,
) -> PlanTaskRead:
    task = PlanService(db).update_task(client_id, task_id, data)
    return PlanTaskRead.model_validate(task)


@router.delete(
    "/tasks/{task_id}",
    response_model=MessageResponse,
    summary="Delete a plan task",
)
def delete_task(
    client_id: uuid.UUID, task_id: uuid.UUID, db: DbSession, _client: RequireClient
) -> MessageResponse:
    PlanService(db).delete_task(client_id, task_id)
    return MessageResponse(detail="Task deleted.")
