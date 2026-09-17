"""Read-tool and write-tool implementations backing the AI command registry.

Read tools query the database directly and never mutate anything. Write tools
never touch the database themselves — every one delegates to a
``ProposalService.stage_*`` call, which dry-run validates the operation
(replaying the real, non-committing half of the underlying service inside a
rolled-back SAVEPOINT) and returns a ``StagedOperation`` for the command agent
to accumulate into the turn's proposal. A tool's Python signature always takes
IDs, never free-text names — the model must call a search tool first to
resolve identity, which is what makes ``request_clarification`` unavoidable
when a search is ambiguous (see ``app/ai/command_agent.py``).
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.enums import (
    ClientCapability,
    ClientStatus,
    TaskCategory,
    TaskPriority,
    TaskStatus,
    UserRole,
)
from app.models.plan import PlanTask
from app.models.user import User
from app.repositories.assignment_repository import AssignmentRepository
from app.schemas.client import ClientUpdate
from app.schemas.onboarding import BrandColorIn, BrandUpdate
from app.schemas.plan import PlanTaskCreate, PlanTaskNoteCreate, PlanTaskUpdate
from app.schemas.user import UserUpdate
from app.services.assignment_service import AssignmentService
from app.services.client_service import ClientService
from app.services.plan_service import PlanService
from app.services.proposal_service import ProposalService, StagedOperation

_MAX_SEARCH_RESULTS = 10


def _user_brief(u: User) -> dict:
    return {"id": str(u.id), "name": u.name, "email": u.email, "role": u.role.value}


def _task_brief(t: PlanTask) -> dict:
    return {
        "id": str(t.id),
        "title": t.title,
        "status": t.status.value,
        "category": t.category.value,
        "priority": t.priority.value,
        "assignee_id": str(t.assignee_id) if t.assignee_id else None,
        "start_date": t.start_date.isoformat() if t.start_date else None,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "archived": t.archived,
    }


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #


def search_plan_tasks(
    db: Session,
    client_id: uuid.UUID,
    _user: User,
    *,
    query: str | None = None,
    status: str | None = None,
    include_archived: bool = False,
    limit: int = 10,
) -> dict:
    plans = PlanService(db)
    rows, total = plans.tasks.list_for_client(
        client_id,
        status=TaskStatus(status) if status else None,
        include_archived=include_archived,
        offset=0,
        limit=200,  # over-fetch, then narrow by title below; still a small bounded page
    )
    if query:
        q = query.lower()
        rows = [r for r in rows if q in r.title.lower()]
    capped = rows[: min(limit, _MAX_SEARCH_RESULTS)]
    return {
        "total_matching": len(rows) if query else total,
        "tasks": [_task_brief(r) for r in capped],
    }


def get_plan_task(db: Session, client_id: uuid.UUID, _user: User, *, task_id: str) -> dict:
    task = PlanService(db).get_task(client_id, uuid.UUID(task_id))
    return _task_brief(task)


def search_users(
    db: Session, client_id: uuid.UUID, _user: User, *, query: str | None = None, limit: int = 10
) -> dict:
    """Search users assigned to this client — never the global user table, so
    this can't enumerate accounts outside the client's own team. Returns name
    *and* email on every match so an ambiguous "John" can be told apart."""
    assignments = AssignmentRepository(db).list_for_client(client_id)
    candidates = [a.user for a in assignments]
    if query:
        q = query.lower()
        candidates = [u for u in candidates if q in u.name.lower() or q in u.email.lower()]
    capped = candidates[: min(limit, _MAX_SEARCH_RESULTS)]
    return {"users": [_user_brief(u) for u in capped]}


def get_client_assignments(db: Session, client_id: uuid.UUID, _user: User) -> dict:
    result = AssignmentService(db).list_for_client(client_id)
    return {
        "assignments": [
            {"user": _user_brief(a.user), "capabilities": [c.value for c in a.capabilities]}
            for a in result.items
        ]
    }


def _client_brief(c: Client) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "business_type": c.business_type,
        "industry": c.industry,
        "website": c.website,
        "location": c.location,
        "language": c.language,
        "timezone": c.timezone,
        "markets": c.markets,
        "status": c.status.value,
        "brand": {
            "about_brand": c.about_brand,
            "brand_voice": c.brand_voice,
            "color_guidelines": c.color_guidelines,
            "logo_url": c.logo_url,
            "colors": [{"hex": color.hex, "label": color.label} for color in c.brand_colors],
            "fonts": [f.family for f in c.brand_fonts],
        },
    }


def get_client(db: Session, client_id: uuid.UUID, user: User) -> dict:
    """Current client settings and brand fields — call this before proposing
    an update so you know the current values and don't have to guess."""
    client = ClientService(db).get_client(user, client_id)  # 404 if inaccessible
    return _client_brief(client)


def get_editable_schema(_db: Session, _client_id: uuid.UUID, _user: User) -> dict:
    """Describes what this command layer can currently create/edit — lets the
    model discover valid field values instead of guessing, and lets a new
    entity/field be added later by extending this dict + the registry, not by
    reworking the agent loop."""
    return {
        "plan_task": {
            "category": [c.value for c in TaskCategory],
            "status": [c.value for c in TaskStatus],
            "priority": [c.value for c in TaskPriority],
        },
        "client_assignment": {"capabilities": [c.value for c in ClientCapability]},
        "client": {"status": [c.value for c in ClientStatus]},
        "user": {"role": [c.value for c in UserRole]},
    }


# --------------------------------------------------------------------------- #
# Write tools (propose-only — see module docstring)
# --------------------------------------------------------------------------- #


def propose_create_plan_task(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    title: str,
    description: str | None = None,
    requirements: str | None = None,
    category: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
) -> StagedOperation:
    data = PlanTaskCreate(
        title=title,
        description=description,
        requirements=requirements,
        category=TaskCategory(category) if category else TaskCategory.strategy,
        status=TaskStatus(status) if status else TaskStatus.todo,
        priority=TaskPriority(priority) if priority else TaskPriority.medium,
        assignee_id=uuid.UUID(assignee_id) if assignee_id else None,
        start_date=date.fromisoformat(start_date) if start_date else None,
        due_date=date.fromisoformat(due_date) if due_date else None,
    )
    return ProposalService(db).stage_plan_task_create(client_id, user, data)


def propose_update_plan_task(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    requirements: str | None = None,
    category: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    archived: bool | None = None,
) -> StagedOperation:
    """Field-level updates only — assignment is a separate pair of tools
    (``propose_assign_user_to_plan_task``/``propose_unassign_plan_task``) so a
    tool call can unambiguously *clear* the assignee, which an "omit the
    field" convention here couldn't express."""
    fields: dict[str, object] = {}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if requirements is not None:
        fields["requirements"] = requirements
    if category is not None:
        fields["category"] = TaskCategory(category)
    if status is not None:
        fields["status"] = TaskStatus(status)
    if priority is not None:
        fields["priority"] = TaskPriority(priority)
    if start_date is not None:
        fields["start_date"] = date.fromisoformat(start_date)
    if due_date is not None:
        fields["due_date"] = date.fromisoformat(due_date)
    if archived is not None:
        fields["archived"] = archived
    data = PlanTaskUpdate(**fields)
    return ProposalService(db).stage_plan_task_update(client_id, user, uuid.UUID(task_id), data)


def propose_assign_user_to_plan_task(
    db: Session, client_id: uuid.UUID, user: User, *, task_id: str, assignee_id: str
) -> StagedOperation:
    data = PlanTaskUpdate(assignee_id=uuid.UUID(assignee_id))
    return ProposalService(db).stage_plan_task_update(client_id, user, uuid.UUID(task_id), data)


def propose_unassign_plan_task(
    db: Session, client_id: uuid.UUID, user: User, *, task_id: str
) -> StagedOperation:
    data = PlanTaskUpdate(assignee_id=None)
    return ProposalService(db).stage_plan_task_update(client_id, user, uuid.UUID(task_id), data)


def propose_archive_plan_task(
    db: Session, client_id: uuid.UUID, user: User, *, task_id: str
) -> StagedOperation:
    data = PlanTaskUpdate(archived=True)
    return ProposalService(db).stage_plan_task_update(client_id, user, uuid.UUID(task_id), data)


def propose_delete_plan_task(
    db: Session, client_id: uuid.UUID, user: User, *, task_id: str
) -> StagedOperation:
    return ProposalService(db).stage_plan_task_delete(client_id, user, uuid.UUID(task_id))


def propose_duplicate_plan_task(
    db: Session, client_id: uuid.UUID, user: User, *, task_id: str
) -> StagedOperation:
    return ProposalService(db).stage_plan_task_duplicate(client_id, user, uuid.UUID(task_id))


def propose_add_plan_task_note(
    db: Session, client_id: uuid.UUID, user: User, *, task_id: str, body: str
) -> StagedOperation:
    return ProposalService(db).stage_plan_task_add_note(
        client_id, user, uuid.UUID(task_id), PlanTaskNoteCreate(body=body)
    )


def propose_assign_user_to_client(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    user_id: str,
    capabilities: list[str] | None = None,
) -> StagedOperation:
    caps = [ClientCapability(c) for c in capabilities] if capabilities else None
    return ProposalService(db).stage_assignment_assign(client_id, user, uuid.UUID(user_id), caps)


def propose_set_client_capabilities(
    db: Session, client_id: uuid.UUID, user: User, *, user_id: str, capabilities: list[str]
) -> StagedOperation:
    caps = [ClientCapability(c) for c in capabilities]
    return ProposalService(db).stage_assignment_set_capabilities(
        client_id, user, uuid.UUID(user_id), caps
    )


def propose_unassign_user_from_client(
    db: Session, client_id: uuid.UUID, user: User, *, user_id: str
) -> StagedOperation:
    return ProposalService(db).stage_assignment_unassign(client_id, user, uuid.UUID(user_id))


def propose_update_client(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    name: str | None = None,
    business_type: str | None = None,
    industry: str | None = None,
    website: str | None = None,
    location: str | None = None,
    language: str | None = None,
    timezone: str | None = None,
    markets: str | None = None,
    status: str | None = None,
) -> StagedOperation:
    fields: dict[str, object] = {}
    for key, value in (
        ("name", name),
        ("business_type", business_type),
        ("industry", industry),
        ("website", website),
        ("location", location),
        ("language", language),
        ("timezone", timezone),
        ("markets", markets),
    ):
        if value is not None:
            fields[key] = value
    if status is not None:
        fields["status"] = ClientStatus(status)
    return ProposalService(db).stage_update_client(client_id, user, ClientUpdate(**fields))


def propose_update_brand(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    about_brand: str | None = None,
    brand_voice: str | None = None,
    color_guidelines: str | None = None,
    logo_url: str | None = None,
    colors: list[dict] | None = None,
    fonts: list[str] | None = None,
) -> StagedOperation:
    fields: dict[str, object] = {}
    for key, value in (
        ("about_brand", about_brand),
        ("brand_voice", brand_voice),
        ("color_guidelines", color_guidelines),
        ("logo_url", logo_url),
    ):
        if value is not None:
            fields[key] = value
    if colors is not None:
        fields["colors"] = [BrandColorIn(hex=c["hex"], label=c.get("label")) for c in colors]
    if fonts is not None:
        fields["fonts"] = fonts
    return ProposalService(db).stage_update_brand(client_id, user, BrandUpdate(**fields))


def propose_update_user(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    user_id: str,
    name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> StagedOperation:
    fields: dict[str, object] = {}
    if name is not None:
        fields["name"] = name
    if role is not None:
        fields["role"] = UserRole(role)
    if is_active is not None:
        fields["is_active"] = is_active
    return ProposalService(db).stage_update_user(
        client_id, user, uuid.UUID(user_id), UserUpdate(**fields)
    )
