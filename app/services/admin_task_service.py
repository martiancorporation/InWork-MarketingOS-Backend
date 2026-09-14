"""Cross-client task-board view for the Admin Panel (BE-04-style aggregation).

Admins see every client's tasks; a manager/user sees only their assigned
clients' tasks — the same scoping ``PlanService``/``CalendarService`` already
enforce per-client, just aggregated. Read-only: no commit needed.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams
from app.models.enums import TaskPriority, TaskStatus, UserRole
from app.models.user import User
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.plan_repository import PlanTaskRepository
from app.schemas.admin_tasks import AdminTaskListResponse, AdminTaskRead
from app.schemas.plan import PlanTaskRead


class AdminTaskService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tasks = PlanTaskRepository(db)
        self.assignments = AssignmentRepository(db)
        self.clients = ClientRepository(db)

    def list_tasks(
        self,
        user: User,
        *,
        pagination: PaginationParams,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        assignee_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        overdue_only: bool = False,
    ) -> AdminTaskListResponse:
        scope = (
            None if user.role == UserRole.admin else self.assignments.list_client_ids_for_user(user.id)
        )
        rows, total = self.tasks.list_across_clients(
            scope,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
            client_id=client_id,
            overdue_only=overdue_only,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        names = {c.id: c.name for c in self.clients.get_many(list({r.client_id for r in rows}))}
        items = [
            AdminTaskRead(
                **PlanTaskRead.model_validate(r).model_dump(),
                client_name=names.get(r.client_id, "—"),
            )
            for r in rows
        ]
        return AdminTaskListResponse(
            items=items, total=total, page=pagination.page, page_size=pagination.page_size
        )
