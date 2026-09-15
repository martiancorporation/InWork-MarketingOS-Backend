"""Data access for plan / task-board items.

Every query is hard-filtered by ``client_id`` so tasks can never leak across
clients — the same tenant-isolation stance the rest of the repositories take.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import and_, case, func, or_, select

from app.models.enums import TaskCategory, TaskPriority, TaskStatus
from app.models.plan import PlanTask, PlanTaskAsset, PlanTaskNote
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
        priority: TaskPriority | None = None,
        assignee_id: uuid.UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        include_undated: bool = False,
        include_archived: bool = False,
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
        same window plus an "unscheduled" pile. Archived tasks are hidden by
        default (``include_archived=True`` to see them) — same "soft-hidden,
        not soft-deleted" stance as everywhere else archiving is used.
        """
        conditions = [PlanTask.client_id == client_id]
        if not include_archived:
            conditions.append(PlanTask.archived.is_(False))
        if status is not None:
            conditions.append(PlanTask.status == status)
        if category is not None:
            conditions.append(PlanTask.category == category)
        if priority is not None:
            conditions.append(PlanTask.priority == priority)
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

    def list_across_clients(
        self,
        client_ids: list[uuid.UUID] | None,
        *,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        assignee_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        overdue_only: bool = False,
        due_within_days: int | None = None,
        include_archived: bool = False,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[PlanTask], int]:
        """Return a page of tasks across clients plus the total matching count.

        ``client_ids=None`` means no scope restriction (admin sees every
        client); a list restricts to those clients (an empty list yields no
        rows) — same scope convention as ``open_counts_for_assignee``. Backs
        the cross-client Admin Panel task view. ``due_within_days`` is the
        forward-looking mirror of ``overdue_only`` — "what's due soon."
        """
        conditions = []
        if client_ids is not None:
            conditions.append(PlanTask.client_id.in_(client_ids))
        if client_id is not None:
            conditions.append(PlanTask.client_id == client_id)
        if not include_archived:
            conditions.append(PlanTask.archived.is_(False))
        if status is not None:
            conditions.append(PlanTask.status == status)
        if priority is not None:
            conditions.append(PlanTask.priority == priority)
        if assignee_id is not None:
            conditions.append(PlanTask.assignee_id == assignee_id)
        if overdue_only:
            conditions.append(PlanTask.due_date < date.today())
            conditions.append(PlanTask.status != TaskStatus.done)
        if due_within_days is not None:
            today = date.today()
            conditions.append(PlanTask.due_date.isnot(None))
            conditions.append(PlanTask.due_date >= today)
            conditions.append(PlanTask.due_date <= today + timedelta(days=due_within_days))
            conditions.append(PlanTask.status != TaskStatus.done)

        total = self.db.scalar(select(func.count()).select_from(PlanTask).where(*conditions))
        # Newest-first, portable across dialects — same rationale as the
        # unwindowed branch of `list_for_client` (cross-DB "nulls last" for a
        # due-date sort is tricky; this view is filterable by overdue instead).
        stmt = (
            select(PlanTask).where(*conditions).order_by(PlanTask.created_at.desc()).offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)

    def workload_summary(
        self, client_ids: list[uuid.UUID] | None
    ) -> list[tuple[uuid.UUID, int, int]]:
        """Per-assignee ``(assignee_id, open_tasks, overdue_tasks)`` across the
        given scope (``None`` = every client). Excludes unassigned and archived
        tasks — a workload view is about real people's queues, not the backlog."""
        today = date.today()
        conditions = [
            PlanTask.assignee_id.isnot(None),
            PlanTask.status != TaskStatus.done,
            PlanTask.archived.is_(False),
        ]
        if client_ids is not None:
            conditions.append(PlanTask.client_id.in_(client_ids))
        is_overdue = case(
            (and_(PlanTask.due_date.isnot(None), PlanTask.due_date < today), 1), else_=0
        )
        rows = self.db.execute(
            select(PlanTask.assignee_id, func.count(), func.sum(is_overdue))
            .where(*conditions)
            .group_by(PlanTask.assignee_id)
        ).all()
        return [
            (assignee_id, int(open_count), int(overdue or 0))
            for assignee_id, open_count, overdue in rows
        ]

    # ---- supporting links ------------------------------------------------ #

    def add_asset(self, asset: PlanTaskAsset) -> None:
        self.db.add(asset)
        self.db.flush()

    def get_asset(self, task_id: uuid.UUID, asset_id: uuid.UUID) -> PlanTaskAsset | None:
        return self.db.scalar(
            select(PlanTaskAsset).where(
                PlanTaskAsset.id == asset_id, PlanTaskAsset.task_id == task_id
            )
        )

    def remove_asset(self, asset: PlanTaskAsset) -> None:
        self.db.delete(asset)
        self.db.flush()

    def next_asset_position(self, task_id: uuid.UUID) -> int:
        count = self.db.scalar(
            select(func.count()).select_from(PlanTaskAsset).where(PlanTaskAsset.task_id == task_id)
        )
        return int(count or 0)

    # ---- notes ------------------------------------------------------------ #

    def add_note(self, note: PlanTaskNote) -> None:
        self.db.add(note)
        self.db.flush()

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
