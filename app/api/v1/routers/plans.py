"""Plan / task-board API (v1) — the internal kanban (todo / in-progress / blocked / done).

- ``GET    /clients/{id}/plan/tasks``                       — list (board columns / filters / date window)
- ``POST   /clients/{id}/plan/tasks``                       — create a task
- ``GET    /clients/{id}/plan/tasks/{task_id}``             — task detail (+ supporting links + notes)
- ``PATCH  /clients/{id}/plan/tasks/{task_id}``             — partial edit (move status / reassign / archive)
- ``DELETE /clients/{id}/plan/tasks/{task_id}``             — remove
- ``POST   /clients/{id}/plan/tasks/{task_id}/duplicate``   — clone as a fresh todo task
- ``POST   /clients/{id}/plan/tasks/{task_id}/assets``      — add a supporting link
- ``DELETE /clients/{id}/plan/tasks/{task_id}/assets/{id}`` — remove a supporting link
- ``POST   /clients/{id}/plan/tasks/{task_id}/notes``       — add a comment

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404, never revealing its
existence. Any user who can see the client may manage its task board.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequireClient
from app.models.enums import TaskCategory, TaskPriority, TaskStatus
from app.schemas.common import MessageResponse
from app.schemas.plan import (
    PlanTaskAssetCreate,
    PlanTaskAssetRead,
    PlanTaskCreate,
    PlanTaskDetailRead,
    PlanTaskListResponse,
    PlanTaskNoteCreate,
    PlanTaskNoteRead,
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
    priority: TaskPriority | None = Query(None),
    assignee_id: uuid.UUID | None = Query(None),
    start: date | None = Query(None, description="Window start (inclusive), YYYY-MM-DD"),
    end: date | None = Query(None, description="Window end (inclusive), YYYY-MM-DD"),
    include_undated: bool = Query(
        False, description="With a window, also return tasks that have no dates at all"
    ),
    include_archived: bool = Query(False, description="Also return archived tasks"),
) -> PlanTaskListResponse:
    return PlanService(db).list_tasks(
        client_id,
        pagination=pagination,
        status=status,
        category=category,
        priority=priority,
        assignee_id=assignee_id,
        start=start,
        end=end,
        include_undated=include_undated,
        include_archived=include_archived,
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


@router.get(
    "/tasks/{task_id}",
    response_model=PlanTaskDetailRead,
    summary="Get a plan task, its supporting links, and its notes",
)
def get_task(
    client_id: uuid.UUID, task_id: uuid.UUID, db: DbSession, _client: RequireClient
) -> PlanTaskDetailRead:
    return PlanService(db).get_task_detail(client_id, task_id)


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


@router.post(
    "/tasks/{task_id}/duplicate",
    response_model=PlanTaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a plan task as a fresh, unassigned todo item",
)
def duplicate_task(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    _client: RequireClient,
) -> PlanTaskRead:
    task = PlanService(db).duplicate_task(client_id, task_id, created_by=user.id)
    return PlanTaskRead.model_validate(task)


@router.post(
    "/tasks/{task_id}/assets",
    response_model=PlanTaskAssetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a supporting link to a plan task",
)
def add_asset(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    data: PlanTaskAssetCreate,
    db: DbSession,
    _client: RequireClient,
) -> PlanTaskAssetRead:
    asset = PlanService(db).add_asset(client_id, task_id, data)
    return PlanTaskAssetRead.model_validate(asset)


@router.delete(
    "/tasks/{task_id}/assets/{asset_id}",
    response_model=MessageResponse,
    summary="Remove a supporting link from a plan task",
)
def remove_asset(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    asset_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
) -> MessageResponse:
    PlanService(db).remove_asset(client_id, task_id, asset_id)
    return MessageResponse(detail="Supporting link removed.")


@router.post(
    "/tasks/{task_id}/notes",
    response_model=PlanTaskNoteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a plan task",
)
def add_note(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    data: PlanTaskNoteCreate,
    user: CurrentUser,
    db: DbSession,
    _client: RequireClient,
) -> PlanTaskNoteRead:
    note = PlanService(db).add_note(client_id, task_id, data, user_id=user.id)
    return PlanTaskNoteRead.model_validate(note)
