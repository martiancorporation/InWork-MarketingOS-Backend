"""Cross-client "what's on you" use-case (BE-04).

Aggregates the current user's outstanding work across every client they can
access. Admins see every client; non-admins are hard-scoped to their assigned
clients. Assigned tasks are always the caller's own; pending approvals and open
alerts are counted per accessible client. Repositories do the grouped counting;
this service merges, sorts, and paginates.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.alert_repository import AlertRepository
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.event_repository import EventRepository
from app.repositories.plan_repository import PlanTaskRepository
from app.schemas.me import MePendingClient, MePendingResponse, MePendingTotals


class MeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.assignments = AssignmentRepository(db)
        self.clients = ClientRepository(db)
        self.tasks = PlanTaskRepository(db)
        self.events = EventRepository(db)
        self.alerts = AlertRepository(db)

    def pending(
        self, user: User, *, pagination: PaginationParams
    ) -> MePendingResponse:
        # Admins aggregate across all clients (None = no client filter);
        # non-admins are restricted to the clients assigned to them.
        if user.role == UserRole.admin:
            scope = None
        else:
            scope = self.assignments.list_client_ids_for_user(user.id)

        task_counts = self.tasks.open_counts_for_assignee(user.id, scope)
        approval_counts = self.events.pending_approval_counts(scope)
        alert_counts = self.alerts.open_counts(scope)

        client_ids = set(task_counts) | set(approval_counts) | set(alert_counts)
        clients = {c.id: c for c in self.clients.get_many(list(client_ids))}

        rows: list[MePendingClient] = []
        totals = MePendingTotals(
            assigned_tasks=0, pending_approvals=0, open_alerts=0, total=0
        )
        for cid in client_ids:
            client = clients.get(cid)
            if client is None:  # defensive: client removed mid-aggregation
                continue
            tasks = task_counts.get(cid, 0)
            approvals = approval_counts.get(cid, 0)
            alerts = alert_counts.get(cid, 0)
            total = tasks + approvals + alerts
            if total == 0:
                continue
            rows.append(
                MePendingClient(
                    client_id=cid,
                    client_name=client.name,
                    client_slug=client.slug,
                    assigned_tasks=tasks,
                    pending_approvals=approvals,
                    open_alerts=alerts,
                    total=total,
                )
            )
            totals.assigned_tasks += tasks
            totals.pending_approvals += approvals
            totals.open_alerts += alerts
            totals.total += total

        # Busiest clients first; stable tie-break by name so paging is deterministic.
        rows.sort(key=lambda r: (-r.total, r.client_name.lower()))
        page = rows[pagination.offset : pagination.offset + pagination.limit]
        return MePendingResponse(
            items=page,
            total=len(rows),
            page=pagination.page,
            page_size=pagination.page_size,
            totals=totals,
        )
