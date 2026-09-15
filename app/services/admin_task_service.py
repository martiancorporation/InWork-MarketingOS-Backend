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
from app.repositories.user_repository import UserRepository
from app.schemas.admin_tasks import (
    AdminTaskListResponse,
    AdminTaskRead,
    TeamWorkloadResponse,
    TeamWorkloadRow,
)
from app.schemas.plan import PlanTaskRead


class AdminTaskService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tasks = PlanTaskRepository(db)
        self.assignments = AssignmentRepository(db)
        self.clients = ClientRepository(db)
        self.users = UserRepository(db)

    def _scope_for(self, user: User) -> list[uuid.UUID] | None:
        """``None`` = every client (admin); otherwise the caller's assigned
        clients — same scoping ``PlanService``/``CalendarService`` enforce
        per-client, aggregated here for the cross-client view."""
        return (
            None
            if user.role == UserRole.admin
            else self.assignments.list_client_ids_for_user(user.id)
        )

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
        due_within_days: int | None = None,
        include_archived: bool = False,
    ) -> AdminTaskListResponse:
        scope = self._scope_for(user)
        rows, total = self.tasks.list_across_clients(
            scope,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
            client_id=client_id,
            overdue_only=overdue_only,
            due_within_days=due_within_days,
            include_archived=include_archived,
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

    def workload(self, user: User) -> TeamWorkloadResponse:
        """Per-team-member open/overdue task counts — the "review team
        workload" and "monitor upcoming deadlines" admin capability, scoped
        the same way as the task list (admin sees everyone, a manager/user
        sees only their assigned clients' workload)."""
        scope = self._scope_for(user)
        rows = self.tasks.workload_summary(scope)
        names = {u.id: u.name for u in self.users.get_many([r[0] for r in rows])}
        items = [
            TeamWorkloadRow(
                user_id=assignee_id,
                user_name=names.get(assignee_id, "—"),
                open_tasks=open_count,
                overdue_tasks=overdue_count,
            )
            for assignee_id, open_count, overdue_count in rows
        ]
        items.sort(key=lambda r: r.open_tasks, reverse=True)
        return TeamWorkloadResponse(items=items)
