"""Plan / task-board use-cases: the internal kanban (todo / in-progress / blocked / done).

Client-access scoping is enforced at the router (via ``ClientService.get_client``)
before any method here runs, so these methods take a ``client_id`` that the
caller is already allowed to see and hard-filter every query by it.

Transaction discipline follows the house rule: the repository only flushes; this
service owns the commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.core.request_context import set_audit_changes
from app.models.enums import TaskCategory, TaskStatus
from app.models.plan import PlanTask
from app.repositories.plan_repository import PlanTaskRepository
from app.schemas.plan import (
    PlanTaskCreate,
    PlanTaskListResponse,
    PlanTaskRead,
    PlanTaskUpdate,
)
from app.services.audit_service import created_changes, deleted_changes


class PlanService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tasks = PlanTaskRepository(db)

    # ---- reads --------------------------------------------------------- #

    def list_tasks(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        status: TaskStatus | None = None,
        category: TaskCategory | None = None,
        assignee_id: uuid.UUID | None = None,
    ) -> PlanTaskListResponse:
        rows, total = self.tasks.list_for_client(
            client_id,
            status=status,
            category=category,
            assignee_id=assignee_id,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        items = [PlanTaskRead.model_validate(t) for t in rows]
        return PlanTaskListResponse(
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_task(self, client_id: uuid.UUID, task_id: uuid.UUID) -> PlanTask:
        task = self.tasks.get_for_client(client_id, task_id)
        if task is None:
            raise NotFoundError("Task not found.")
        return task

    # ---- writes -------------------------------------------------------- #

    def create_task(
        self, client_id: uuid.UUID, data: PlanTaskCreate, *, created_by: uuid.UUID
    ) -> PlanTask:
        task = PlanTask(
            client_id=client_id,
            title=data.title,
            description=data.description,
            category=data.category,
            status=data.status,
            assignee_id=data.assignee_id,
            due_date=data.due_date,
            created_by=created_by,
        )
        self.tasks.add(task)
        self.tasks.flush()  # assign the id before returning
        set_audit_changes(
            created_changes({"title": task.title, "category": task.category, "status": task.status})
        )
        self.db.commit()
        return self.get_task(client_id, task.id)

    def update_task(
        self, client_id: uuid.UUID, task_id: uuid.UUID, data: PlanTaskUpdate
    ) -> PlanTask:
        task = self.get_task(client_id, task_id)
        fields = data.model_fields_set
        for attr in (
            "title",
            "description",
            "category",
            "status",
            "assignee_id",
            "due_date",
        ):
            if attr in fields:
                setattr(task, attr, getattr(data, attr))
        self.db.commit()
        return self.get_task(client_id, task.id)

    def delete_task(self, client_id: uuid.UUID, task_id: uuid.UUID) -> None:
        task = self.get_task(client_id, task_id)
        set_audit_changes(
            deleted_changes({"title": task.title, "category": task.category, "status": task.status})
        )
        self.db.delete(task)
        self.db.commit()
