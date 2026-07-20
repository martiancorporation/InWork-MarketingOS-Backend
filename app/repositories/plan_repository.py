"""Data access for plan / task-board items.

Every query is hard-filtered by ``client_id`` so tasks can never leak across
clients — the same tenant-isolation stance the rest of the repositories take.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.enums import TaskCategory, TaskStatus
from app.models.plan import PlanTask
from app.repositories.base import BaseRepository


class PlanTaskRepository(BaseRepository[PlanTask]):
    model = PlanTask

    def get_for_client(
        self, client_id: uuid.UUID, task_id: uuid.UUID
    ) -> PlanTask | None:
        """Load one task scoped to a client."""
        return self.db.scalar(
            select(PlanTask).where(
                PlanTask.id == task_id,
                PlanTask.client_id == client_id,
            )
        )

    def list_for_client(
        self,
        client_id: uuid.UUID,
        *,
        status: TaskStatus | None = None,
        category: TaskCategory | None = None,
        assignee_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[PlanTask], int]:
        """Return a page of tasks plus the total matching count (DB-side)."""
        conditions = [PlanTask.client_id == client_id]
        if status is not None:
            conditions.append(PlanTask.status == status)
        if category is not None:
            conditions.append(PlanTask.category == category)
        if assignee_id is not None:
            conditions.append(PlanTask.assignee_id == assignee_id)

        total = self.db.scalar(
            select(func.count()).select_from(PlanTask).where(*conditions)
        )
        # ``due_date asc nulls last`` is tricky cross-DB; newest-first by creation
        # is simple and portable, and matches the board's "recently added" default.
        stmt = (
            select(PlanTask)
            .where(*conditions)
            .order_by(PlanTask.created_at.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)

    def open_counts_for_assignee(
        self,
        assignee_id: uuid.UUID,
        client_ids: list[uuid.UUID] | None = None,
    ) -> dict[uuid.UUID, int]:
        """Count non-done tasks assigned to a user, grouped by client.

        ``client_ids=None`` counts across every client; a list restricts to those
        clients (an empty list yields no rows). Backs the cross-client
        "what's on you" view (BE-04).
        """
        conditions = [
            PlanTask.assignee_id == assignee_id,
            PlanTask.status != TaskStatus.done,
        ]
        if client_ids is not None:
            conditions.append(PlanTask.client_id.in_(client_ids))
        rows = self.db.execute(
            select(PlanTask.client_id, func.count())
            .where(*conditions)
            .group_by(PlanTask.client_id)
        ).all()
        return {cid: int(n) for cid, n in rows}

    def completion_counts(self, client_id: uuid.UUID) -> tuple[int, int]:
        """Return ``(done, total)`` task counts for a client (BE-06 adherence)."""
        total = self.db.scalar(
            select(func.count()).select_from(PlanTask).where(
                PlanTask.client_id == client_id
            )
        )
        done = self.db.scalar(
            select(func.count()).select_from(PlanTask).where(
                PlanTask.client_id == client_id,
                PlanTask.status == TaskStatus.done,
            )
        )
        return int(done or 0), int(total or 0)
