"""Data access for plan / task-board items.

Every query is hard-filtered by ``client_id`` so tasks can never leak across
clients — the same tenant-isolation stance the rest of the repositories take.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import and_, func, or_, select

from app.models.enums import TaskCategory, TaskStatus
from app.models.plan import PlanTask
from app.repositories.base import BaseRepository


class PlanTaskRepository(BaseRepository[PlanTask]):
    model = PlanTask

    def get_for_client(self, client_id: uuid.UUID, task_id: uuid.UUID) -> PlanTask | None:
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
        start: date | None = None,
        end: date | None = None,
        include_undated: bool = False,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[PlanTask], int]:
        """Return a page of tasks plus the total matching count (DB-side).

        ``start``/``end`` select tasks whose span *overlaps* that window, which is
        what a calendar needs: a campaign running 1–31 July must appear when
        viewing a week in the middle of it, not only when its edges fall inside
        the window.

        A windowed query drops undated tasks, since a calendar has nowhere to draw
        them. ``include_undated`` adds them back for the board, which shows the
        same window plus an "unscheduled" pile.
        """
        conditions = [PlanTask.client_id == client_id]
        if status is not None:
            conditions.append(PlanTask.status == status)
        if category is not None:
            conditions.append(PlanTask.category == category)
        if assignee_id is not None:
            conditions.append(PlanTask.assignee_id == assignee_id)

        windowed = start is not None or end is not None
        if windowed:
            # A one-sided task (only start_date or only due_date) is a single-day
            # item, so coalesce collapses the span to that one day.
            span_start = func.coalesce(PlanTask.start_date, PlanTask.due_date)
            span_end = func.coalesce(PlanTask.due_date, PlanTask.start_date)
            overlaps = [PlanTask.start_date.isnot(None) | PlanTask.due_date.isnot(None)]
            if end is not None:
                overlaps.append(span_start <= end)
            if start is not None:
                overlaps.append(span_end >= start)
            in_window = and_(*overlaps)
            if include_undated:
                # The board needs its "unscheduled" pile alongside the window; a
                # calendar does not (and would have nowhere to draw them).
                conditions.append(
                    or_(
                        in_window,
                        and_(PlanTask.start_date.is_(None), PlanTask.due_date.is_(None)),
                    )
                )
            else:
                conditions.append(in_window)

        total = self.db.scalar(select(func.count()).select_from(PlanTask).where(*conditions))
        if windowed:
            # Chronological for a calendar; ``coalesce`` keeps one-sided tasks in
            # place and sidesteps the cross-DB "nulls last" problem below.
            order = (func.coalesce(PlanTask.start_date, PlanTask.due_date).asc(), PlanTask.id.asc())
        else:
            # ``due_date asc nulls last`` is tricky cross-DB; newest-first by creation
            # is simple and portable, and matches the board's "recently added" default.
            order = (PlanTask.created_at.desc(),)
        stmt = select(PlanTask).where(*conditions).order_by(*order).offset(offset)
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
            select(PlanTask.client_id, func.count()).where(*conditions).group_by(PlanTask.client_id)
        ).all()
        return {cid: int(n) for cid, n in rows}

    def completion_counts(self, client_id: uuid.UUID) -> tuple[int, int]:
        """Return ``(done, total)`` task counts for a client (BE-06 adherence)."""
        total = self.db.scalar(
            select(func.count()).select_from(PlanTask).where(PlanTask.client_id == client_id)
        )
        done = self.db.scalar(
            select(func.count())
            .select_from(PlanTask)
            .where(
                PlanTask.client_id == client_id,
                PlanTask.status == TaskStatus.done,
            )
        )
        return int(done or 0), int(total or 0)
