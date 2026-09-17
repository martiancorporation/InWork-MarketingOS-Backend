"""Plan / task-board use-cases: the internal kanban (todo / in-progress / blocked / done).

Client-access scoping is enforced at the router (via ``ClientService.get_client``)
before any method here runs, so these methods take a ``client_id`` that the
caller is already allowed to see and hard-filter every query by it.

Transaction discipline follows the house rule: the repository only flushes; this
service owns the commit.
"""

from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.pagination import PaginationParams
from app.core.request_context import set_audit_changes
from app.models.enums import TaskCategory, TaskPriority, TaskStatus
from app.models.plan import PlanTask, PlanTaskAsset, PlanTaskNote
from app.repositories.plan_repository import PlanTaskRepository
from app.schemas.plan import (
    PlanTaskAssetCreate,
    PlanTaskCreate,
    PlanTaskDetailRead,
    PlanTaskListResponse,
    PlanTaskNoteCreate,
    PlanTaskRead,
    PlanTaskUpdate,
)
from app.services.audit_service import created_changes, deleted_changes, field_changes

#: Fields a PATCH may move, and which the audit diff tracks.
_MUTABLE = (
    "title",
    "description",
    "requirements",
    "category",
    "status",
    "priority",
    "assignee_id",
    "start_date",
    "due_date",
    "start_time",
    "end_time",
    "archived",
)
_MAX_ASSETS_PER_TASK = 20


def _audit_value(value: object) -> object:
    """JSON-safe scalar for an audit diff.

    ``field_changes`` compares values as-is and its callers are responsible for
    passing JSON-safe scalars — a raw ``date``/``time`` would fail the JSONB
    insert, and the audit middleware swallows that failure, so the row would
    disappear silently rather than loudly.
    """
    if isinstance(value, date | time):
        return value.isoformat()
    return getattr(value, "value", value)


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
        priority: TaskPriority | None = None,
        assignee_id: uuid.UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        include_undated: bool = False,
        include_archived: bool = False,
    ) -> PlanTaskListResponse:
        if start is not None and end is not None and start > end:
            raise BadRequestError("start must be on or before end")
        rows, total = self.tasks.list_for_client(
            client_id,
            status=status,
            category=category,
            priority=priority,
            assignee_id=assignee_id,
            start=start,
            end=end,
            include_undated=include_undated,
            include_archived=include_archived,
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

    def get_task_detail(self, client_id: uuid.UUID, task_id: uuid.UUID) -> PlanTaskDetailRead:
        task = self.get_task(client_id, task_id)
        return PlanTaskDetailRead.model_validate(task)

    # ---- writes -------------------------------------------------------- #

    def create_task(
        self, client_id: uuid.UUID, data: PlanTaskCreate, *, created_by: uuid.UUID
    ) -> PlanTask:
        task, changes = self._apply_create_task(client_id, data, created_by=created_by)
        if changes:
            set_audit_changes(changes)
        self.db.commit()
        return self.get_task(client_id, task.id)

    def _apply_create_task(
        self, client_id: uuid.UUID, data: PlanTaskCreate, *, created_by: uuid.UUID
    ) -> tuple[PlanTask, dict | None]:
        """Everything ``create_task`` does short of the commit — the reusable
        core the AI proposal engine dry-runs inside a rolled-back SAVEPOINT
        (see ``ProposalService``) and replays for real at execution time."""
        task = PlanTask(
            client_id=client_id,
            title=data.title,
            description=data.description,
            requirements=data.requirements,
            category=data.category,
            status=data.status,
            priority=data.priority,
            assignee_id=data.assignee_id,
            start_date=data.start_date,
            due_date=data.due_date,
            start_time=data.start_time,
            end_time=data.end_time,
            created_by=created_by,
        )
        self.tasks.add(task)
        self.tasks.flush()  # assign the id before returning
        changes = created_changes(
            {
                "title": task.title,
                "category": task.category,
                "status": task.status,
                # Coerced: ``created_changes`` only unwraps enums, so a raw
                # date would break the JSONB insert.
                "start_date": _audit_value(task.start_date),
                "due_date": _audit_value(task.due_date),
            }
        )
        return task, changes

    def update_task(
        self, client_id: uuid.UUID, task_id: uuid.UUID, data: PlanTaskUpdate
    ) -> PlanTask:
        task, changes = self._apply_update_task(client_id, task_id, data)
        if changes:
            set_audit_changes(changes)
        self.db.commit()
        return self.get_task(client_id, task.id)

    def _apply_update_task(
        self, client_id: uuid.UUID, task_id: uuid.UUID, data: PlanTaskUpdate
    ) -> tuple[PlanTask, dict | None]:
        task = self.get_task(client_id, task_id)
        fields = data.model_fields_set
        touched = [a for a in _MUTABLE if a in fields]

        # A partial patch can invert a range by moving only one edge, which the
        # request schema cannot catch on its own — it only sees what was sent. So
        # validate the *merged* result, and do it before touching the ORM object:
        # mutating first would leave the session dirty on the failure path.
        def merged(attr: str) -> object:
            return getattr(data, attr) if attr in fields else getattr(task, attr)

        if (s := merged("start_date")) and (e := merged("due_date")) and s > e:  # type: ignore[operator]
            raise BadRequestError("start_date must be on or before due_date")
        if (t0 := merged("start_time")) and (t1 := merged("end_time")) and t0 > t1:  # type: ignore[operator]
            raise BadRequestError("start_time must be on or before end_time")

        before = {a: _audit_value(getattr(task, a)) for a in touched}
        for attr in touched:
            setattr(task, attr, getattr(data, attr))
        after = {a: _audit_value(getattr(task, a)) for a in touched}
        return task, field_changes(before, after)

    def delete_task(self, client_id: uuid.UUID, task_id: uuid.UUID) -> None:
        _task, changes = self._apply_delete_task(client_id, task_id)
        if changes:
            set_audit_changes(changes)
        self.db.commit()

    def _apply_delete_task(
        self, client_id: uuid.UUID, task_id: uuid.UUID
    ) -> tuple[PlanTask, dict | None]:
        task = self.get_task(client_id, task_id)
        changes = deleted_changes(
            {"title": task.title, "category": task.category, "status": task.status}
        )
        self.db.delete(task)
        self.db.flush()
        return task, changes

    def duplicate_task(
        self, client_id: uuid.UUID, task_id: uuid.UUID, *, created_by: uuid.UUID
    ) -> PlanTask:
        """The context-menu "Duplicate" action — a fresh, unassigned, todo copy
        of an existing task (title/description/requirements/category/priority/
        dates carried over; status and assignee deliberately reset, since a
        duplicate is a new piece of work, not a clone of someone's in-progress
        item)."""
        copy, changes = self._apply_duplicate_task(client_id, task_id, created_by=created_by)
        if changes:
            set_audit_changes(changes)
        self.db.commit()
        return self.get_task(client_id, copy.id)

    def _apply_duplicate_task(
        self, client_id: uuid.UUID, task_id: uuid.UUID, *, created_by: uuid.UUID
    ) -> tuple[PlanTask, dict | None]:
        source = self.get_task(client_id, task_id)
        copy = PlanTask(
            client_id=client_id,
            title=f"{source.title} (copy)"[:200],
            description=source.description,
            requirements=source.requirements,
            category=source.category,
            status=TaskStatus.todo,
            priority=source.priority,
            start_date=source.start_date,
            due_date=source.due_date,
            start_time=source.start_time,
            end_time=source.end_time,
            created_by=created_by,
        )
        self.tasks.add(copy)
        self.tasks.flush()
        changes = created_changes({"title": copy.title, "category": copy.category})
        return copy, changes

    # ---- supporting links ------------------------------------------------ #

    def add_asset(
        self, client_id: uuid.UUID, task_id: uuid.UUID, data: PlanTaskAssetCreate
    ) -> PlanTaskAsset:
        task = self.get_task(client_id, task_id)
        if len(task.assets) >= _MAX_ASSETS_PER_TASK:
            raise BadRequestError(
                f"A task can have at most {_MAX_ASSETS_PER_TASK} supporting links."
            )
        asset = PlanTaskAsset(
            task_id=task.id,
            url=data.url,
            label=data.label,
            position=self.tasks.next_asset_position(task.id),
        )
        self.tasks.add_asset(asset)
        self.db.commit()
        return asset

    def remove_asset(self, client_id: uuid.UUID, task_id: uuid.UUID, asset_id: uuid.UUID) -> None:
        self.get_task(client_id, task_id)  # 404 for an inaccessible/missing task
        asset = self.tasks.get_asset(task_id, asset_id)
        if asset is None:
            raise NotFoundError("Supporting link not found.")
        self.tasks.remove_asset(asset)
        self.db.commit()

    # ---- notes -------------------------------------------------------------- #

    def add_note(
        self,
        client_id: uuid.UUID,
        task_id: uuid.UUID,
        data: PlanTaskNoteCreate,
        *,
        user_id: uuid.UUID,
    ) -> PlanTaskNote:
        note, _task = self._apply_add_note(client_id, task_id, data, user_id=user_id)
        self.db.commit()
        return note

    def _apply_add_note(
        self,
        client_id: uuid.UUID,
        task_id: uuid.UUID,
        data: PlanTaskNoteCreate,
        *,
        user_id: uuid.UUID,
    ) -> tuple[PlanTaskNote, PlanTask]:
        task = self.get_task(client_id, task_id)
        note = PlanTaskNote(task_id=task.id, user_id=user_id, body=data.body)
        self.tasks.add_note(note)
        self.tasks.flush()
        return note, task
