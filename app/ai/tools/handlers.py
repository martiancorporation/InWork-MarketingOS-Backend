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

from app.core.exceptions import AppError
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
from app.services.proposal_service import ProposalService, StagedBatch, StagedOperation

_MAX_SEARCH_RESULTS = 10
#: Safety cap on how many tasks one bulk propose_* call may stage — matches
#: this codebase's "bound all inputs/outputs" house rule. A genuinely bigger
#: change should be narrowed by the model (a tighter range/filter) or split
#: across several calls, not staged as one unbounded batch.
_MAX_BULK_MATCHES = 100


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


def _resolve_date_window(
    *, on_date: str | None, start_date: str | None, end_date: str | None
) -> tuple[date | None, date | None]:
    """Shared by every task-search/bulk tool: a single named day (on_date) or
    a real range (start_date+end_date, either edge optional meaning "open
    ended" is NOT supported here — both must be given together, since an
    unbounded range would defeat the whole point of the bulk-size cap)."""
    if on_date:
        day = date.fromisoformat(on_date)
        return day, day
    if start_date or end_date:
        if not (start_date and end_date):
            raise ValueError("Provide both start_date and end_date together for a range.")
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if end < start:
            raise ValueError("end_date must be on or after start_date.")
        return start, end
    return None, None


def _apply_assignee_filter(
    rows: list[PlanTask], *, assignee_id: str | None, assigned_only: bool
) -> list[PlanTask]:
    """``assignee_id`` narrows to one specific person (resolve via
    search_users first); ``assigned_only`` narrows to "has ANY assignee" —
    the filter a request like "assigned to someone" / "anyone assigned" /
    "only assigned tasks" needs, distinct from any one person. Combining both
    means "assigned to this specific person" (assigned_only is then
    redundant but harmless). Neither set = no assignee filtering at all."""
    if assignee_id:
        wanted = uuid.UUID(assignee_id)
        rows = [r for r in rows if r.assignee_id == wanted]
    elif assigned_only:
        rows = [r for r in rows if r.assignee_id is not None]
    return rows


def search_plan_tasks(
    db: Session,
    client_id: uuid.UUID,
    _user: User,
    *,
    query: str | None = None,
    status: str | None = None,
    on_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    assignee_id: str | None = None,
    assigned_only: bool = False,
    include_archived: bool = False,
    limit: int = 10,
) -> dict:
    plans = PlanService(db)
    start, end = _resolve_date_window(on_date=on_date, start_date=start_date, end_date=end_date)
    rows, total = plans.tasks.list_for_client(
        client_id,
        status=TaskStatus(status) if status else None,
        start=start,
        end=end,
        include_undated=start is None,
        include_archived=include_archived,
        offset=0,
        limit=_MAX_BULK_MATCHES + 1,  # over-fetch, then narrow by title below
    )
    if query:
        q = query.lower()
        rows = [r for r in rows if q in r.title.lower()]
    rows = _apply_assignee_filter(rows, assignee_id=assignee_id, assigned_only=assigned_only)
    capped = rows[: min(limit, _MAX_SEARCH_RESULTS)]
    narrowed = bool(query or start or assignee_id or assigned_only)
    return {
        "total_matching": len(rows) if narrowed else total,
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
        "client": {"status": [c.value for c in ClientStatus]},
    }


def search_knowledge_base(db: Session, client_id: uuid.UUID, _user: User, *, query: str) -> dict:
    """Semantic search over this client's own indexed knowledge (brand voice,
    goals, compliance rules, onboarding answers, uploaded documents) — call
    this to answer any question about the client rather than guessing. Hard
    client-scoped: the retrieval query is filtered by ``client_id`` at the SQL
    level (``KnowledgeChunkRepository.search``), so this can never surface
    another client's data regardless of what is asked."""
    from app.integrations.embeddings import get_embedder
    from app.services.intelligence.context_service import ContextService

    result = ContextService(db, get_embedder()).build(client_id, query=query, top_k=6)
    snippets = [chunk.text for chunk, _score in result.retrieved]
    if not snippets:
        return {"snippets": [], "note": "No indexed knowledge matched this query."}
    return {"snippets": snippets}


def get_performance_summary(
    db: Session, client_id: uuid.UUID, _user: User, *, days: int = 30
) -> dict:
    """Real ad-performance numbers (spend, leads, conversions, ...) for this
    client over the trailing window — call this for "how are my ads doing"
    style questions."""
    from datetime import date, timedelta

    from app.services.analytics_service import AnalyticsService

    window = max(1, min(days, 90))
    end = date.today()
    start = end - timedelta(days=window)
    summary = AnalyticsService(db).summary(client_id, start=start, end=end)
    t = summary.totals
    return {
        "window_days": window,
        "data_as_of": summary.data_as_of.isoformat() if summary.data_as_of else None,
        "stale": summary.stale,
        "spend": t.spend,
        "impressions": t.impressions,
        "clicks": t.clicks,
        "ctr_percent": t.ctr,
        "leads": t.leads,
        "cost_per_lead": t.cpl,
        "conversions": t.conversions,
        "revenue": t.revenue,
        "roas": t.roas,
        "by_platform": [
            {
                "platform": p.platform.value,
                "spend": p.spend,
                "impressions": p.impressions,
                "clicks": p.clicks,
                "leads": p.leads,
            }
            for p in summary.by_platform
        ],
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


def _resolve_bulk_task_ids(
    db: Session,
    client_id: uuid.UUID,
    *,
    task_ids: list[str] | None,
    start_date: str | None,
    end_date: str | None,
    status: str | None,
    query: str | None,
    assignee_id: str | None,
    assigned_only: bool,
    include_archived: bool,
) -> list[uuid.UUID]:
    """The scope for a bulk propose_* call — an explicit id list, or every
    task matching a date range (+ optional narrowing, including who it's
    assigned to — see _apply_assignee_filter), capped at _MAX_BULK_MATCHES so
    one tool call can never stage an unbounded batch."""
    if task_ids:
        return [uuid.UUID(t) for t in task_ids]
    start, end = _resolve_date_window(on_date=None, start_date=start_date, end_date=end_date)
    if start is None:
        raise ValueError("Provide either task_ids or both start_date and end_date.")
    plans = PlanService(db)
    rows, _total = plans.tasks.list_for_client(
        client_id,
        status=TaskStatus(status) if status else None,
        start=start,
        end=end,
        include_undated=False,
        include_archived=include_archived,
        offset=0,
        limit=_MAX_BULK_MATCHES + 1,
    )
    if query:
        q = query.lower()
        rows = [r for r in rows if q in r.title.lower()]
    rows = _apply_assignee_filter(rows, assignee_id=assignee_id, assigned_only=assigned_only)
    if len(rows) > _MAX_BULK_MATCHES:
        raise ValueError(
            f"That scope matches {len(rows)} tasks, more than the {_MAX_BULK_MATCHES}-task "
            "bulk-operation limit — narrow the date range or add a query/status/assignee "
            "filter, then try again."
        )
    return [r.id for r in rows]


def propose_bulk_update_plan_tasks(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    task_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    query: str | None = None,
    status: str | None = None,
    assignee_id: str | None = None,
    assigned_only: bool = False,
    include_archived: bool = False,
    new_status: str | None = None,
    priority: str | None = None,
    category: str | None = None,
    archived: bool | None = None,
) -> StagedBatch:
    fields: dict[str, object] = {}
    if new_status is not None:
        fields["status"] = TaskStatus(new_status)
    if priority is not None:
        fields["priority"] = TaskPriority(priority)
    if category is not None:
        fields["category"] = TaskCategory(category)
    if archived is not None:
        fields["archived"] = archived
    if not fields:
        raise ValueError(
            "Provide at least one field to change: new_status, priority, category, or archived."
        )
    ids = _resolve_bulk_task_ids(
        db,
        client_id,
        task_ids=task_ids,
        start_date=start_date,
        end_date=end_date,
        status=status,
        query=query,
        assignee_id=assignee_id,
        assigned_only=assigned_only,
        include_archived=include_archived,
    )
    if not ids:
        raise ValueError("No tasks matched that scope — nothing to change.")
    data = PlanTaskUpdate(**fields)
    svc = ProposalService(db)
    operations: list[StagedOperation] = []
    skipped: list[dict] = []
    for task_id in ids:
        try:
            operations.append(svc.stage_plan_task_update(client_id, user, task_id, data))
        except AppError as exc:
            skipped.append({"task_id": str(task_id), "reason": exc.message})
    return StagedBatch(operations=operations, skipped=skipped)


def propose_bulk_delete_plan_tasks(
    db: Session,
    client_id: uuid.UUID,
    user: User,
    *,
    task_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    query: str | None = None,
    status: str | None = None,
    assignee_id: str | None = None,
    assigned_only: bool = False,
    include_archived: bool = False,
) -> StagedBatch:
    ids = _resolve_bulk_task_ids(
        db,
        client_id,
        task_ids=task_ids,
        start_date=start_date,
        end_date=end_date,
        status=status,
        query=query,
        assignee_id=assignee_id,
        assigned_only=assigned_only,
        include_archived=include_archived,
    )
    if not ids:
        raise ValueError("No tasks matched that scope — nothing to delete.")
    svc = ProposalService(db)
    operations: list[StagedOperation] = []
    skipped: list[dict] = []
    for task_id in ids:
        try:
            operations.append(svc.stage_plan_task_delete(client_id, user, task_id))
        except AppError as exc:
            skipped.append({"task_id": str(task_id), "reason": exc.message})
    return StagedBatch(operations=operations, skipped=skipped)


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
