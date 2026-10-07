"""The AI command layer's tool registry.

Every tool the command agent (``app/ai/command_agent.py``) can call is
declared here: its name, its OpenAI-style JSON-Schema parameters (what the
model sees), its Python handler (``app/ai/tools/handlers.py``), and its
``kind``:

- ``"read"``  — executes immediately against the database; never mutates.
- ``"write"`` — never mutates either: the handler always returns a
  ``StagedOperation`` (a dry-run-validated draft), which the agent
  accumulates into the turn's ``ChangeProposal``. Nothing is written until a
  human calls the approve endpoint (``ProposalService.approve``).
- ``"clarify"`` — the single non-mutating escape hatch a model must use when
  a search is ambiguous or a required detail is missing; ends the turn with a
  question instead of a guess.

Adding a new editable field or entity is adding one function to ``handlers``
and one entry below — the agent loop and the approval/execution engine need
no changes (see the architecture plan's extensibility goal).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.ai.tools import handlers

ToolKind = Literal["read", "write", "clarify"]

REQUEST_CLARIFICATION = "request_clarification"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object) for the tool's arguments
    kind: ToolKind
    handler: Callable[..., Any] | None = None  # None only for "clarify"
    #: Human-friendly "what I'm doing right now" label for the streaming
    #: turn endpoint's live progress indicator. Falls back to a generic
    #: label (see `progress_label_for`) when left blank.
    progress_label: str = ""


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_TOOLS: list[ToolSpec] = [
    # ---- read ------------------------------------------------------------ #
    ToolSpec(
        name="search_plan_tasks",
        description=(
            "Find existing task(s) on this client's plan/task board — REQUIRED before "
            "propose_update_plan_task, propose_assign_user_to_plan_task, "
            "propose_unassign_plan_task, or propose_archive_plan_task, since every one "
            "of those needs a real task_id and none of them may be guessed. Whenever "
            "the user refers to something that already exists ('this plan', 'that "
            "task', 'the one for the 26th', 'the launch post'), call this first — use "
            "on_date for a single named day, start_date+end_date for anything wider "
            "(a week, a month, several months, a year, an explicit range — resolve the "
            "user's wording into concrete dates yourself), query when they name/describe "
            "it. Also useful to preview how many tasks a range actually covers before "
            "calling a bulk propose tool on that same range. total_matching in the "
            "response is the real total count even when more results exist than fit in "
            "the returned list. Try ONE well-targeted call; if it doesn't return a clear "
            "match (or, for a range, if the count is surprising), call "
            "request_clarification immediately rather than retrying with different "
            "search terms."
        ),
        parameters=_obj(
            {
                "query": {"type": "string", "description": "Substring match on the task title."},
                "status": {"type": "string", "enum": ["todo", "in_progress", "blocked", "done"]},
                "on_date": {
                    "type": "string",
                    "description": (
                        "YYYY-MM-DD. Finds task(s) scheduled on this exact date (start_date "
                        "through due_date span includes it) — use this whenever the user "
                        'names or implies a single specific date ("the plan for the 26th", '
                        "\"tomorrow's post\"). Don't combine with start_date/end_date."
                    ),
                },
                "start_date": {
                    "type": "string",
                    "description": (
                        "YYYY-MM-DD. Start of a date/week/month/year range — pair with "
                        "end_date. Use this (not on_date) for anything wider than a single "
                        "day: a week, a specific week range, a month, several months, a "
                        "month range, a year, several years, a year range, or an explicit "
                        "start/end the user gave you."
                    ),
                },
                "end_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD. End of the range (inclusive) — pair with start_date.",
                },
                "assignee_id": {
                    "type": "string",
                    "description": (
                        "A user id from search_users — narrows to tasks assigned to that one "
                        "person ('assigned to John'). Resolve the person first; never guess."
                    ),
                },
                "assigned_only": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "True narrows to tasks that have ANY assignee, regardless of who — "
                        "use this for 'assigned to someone'/'anyone assigned'/'only assigned "
                        "tasks', which name no specific person (don't set assignee_id for "
                        "that; assignee_id is for one named person). Leave both unset to "
                        "include tasks either way, including unassigned ones."
                    ),
                },
                "include_archived": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
            }
        ),
        kind="read",
        handler=handlers.search_plan_tasks,
        progress_label="Searching plan tasks",
    ),
    ToolSpec(
        name="get_plan_task",
        description="Get one plan task by id.",
        parameters=_obj({"task_id": {"type": "string"}}, required=["task_id"]),
        kind="read",
        handler=handlers.get_plan_task,
        progress_label="Looking up the task",
    ),
    ToolSpec(
        name="search_users",
        description=(
            "Search team members who already have access to this client, by name or "
            "email — to find who to assign a plan task to. Always call this before "
            "assigning anyone; if more than one result matches, you MUST call "
            "request_clarification and show the user their names and emails rather "
            "than guessing which one was meant. This is NOT for managing who has "
            "access to the client itself — that is handled outside this chat, in the "
            "agency dashboard."
        ),
        parameters=_obj(
            {
                "query": {"type": "string", "description": "Substring match on name or email."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
            }
        ),
        kind="read",
        handler=handlers.search_users,
        progress_label="Searching users",
    ),
    ToolSpec(
        name="search_knowledge_base",
        description=(
            "Search this client's own knowledge — brand voice, goals, compliance "
            "rules, onboarding answers, uploaded documents. Call this to answer any "
            'question about the client ("what\'s our brand voice", "what are we not '
            'allowed to say", "what are this client\'s goals") instead of guessing '
            "or answering from general knowledge."
        ),
        parameters=_obj({"query": {"type": "string"}}, required=["query"]),
        kind="read",
        handler=handlers.search_knowledge_base,
        progress_label="Searching client knowledge",
    ),
    ToolSpec(
        name="get_performance_summary",
        description=(
            "Real ad-performance numbers for this client (spend, impressions, clicks, "
            "leads, conversions, revenue, ROAS) over a trailing window. Call this for "
            '"how are my ads doing" / "what\'s our spend" style questions.'
        ),
        parameters=_obj(
            {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 90,
                    "default": 30,
                    "description": "Trailing window size in days.",
                }
            }
        ),
        kind="read",
        handler=handlers.get_performance_summary,
        progress_label="Checking performance data",
    ),
    ToolSpec(
        name="get_client",
        description=(
            "Get this client's current settings and brand fields. Call this before "
            "proposing an update to a client or its brand so you know the current "
            "values."
        ),
        parameters=_obj({}),
        kind="read",
        handler=handlers.get_client,
        progress_label="Reading client settings",
    ),
    ToolSpec(
        name="get_editable_schema",
        description=(
            "Returns the valid enum values (status, category, priority, capabilities, "
            "...) for every editable entity this command layer supports. Call this if "
            "you are unsure which values are valid before proposing a change."
        ),
        parameters=_obj({}),
        kind="read",
        handler=handlers.get_editable_schema,
        progress_label="Checking valid field values",
    ),
    # ---- write (propose-only) --------------------------------------------- #
    ToolSpec(
        name="propose_create_plan_task",
        description=(
            "Draft a BRAND NEW task that does not exist yet. Never use this when the "
            'user is referring to something that already exists ("this plan", "that '
            'task", "the one for the 26th") — call search_plan_tasks first, and if a '
            "matching task is found, use propose_update_plan_task / "
            "propose_assign_user_to_plan_task on its id instead. Requires human "
            "approval before it exists.\n\n"
            "Title style: write it exactly as a person would — never prefix or append "
            "a platform name (no 'Instagram Reel:', 'Facebook Static —', etc.). This "
            "tool has no platform/format fields, so that detail belongs in the "
            "description if it matters at all, not the title."
        ),
        parameters=_obj(
            {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "requirements": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": [
                        "strategy",
                        "creative",
                        "ads",
                        "content",
                        "analytics",
                        "compliance",
                        "admin",
                    ],
                },
                "status": {"type": "string", "enum": ["todo", "in_progress", "blocked", "done"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "assignee_id": {"type": "string", "description": "A user id from search_users."},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            required=["title"],
        ),
        kind="write",
        handler=handlers.propose_create_plan_task,
        progress_label="Drafting a new task",
    ),
    ToolSpec(
        name="propose_update_plan_task",
        description=(
            "Change one or more fields (title, description, requirements, category, "
            "status, priority, dates, archived) on an EXISTING task — you must already "
            "have its task_id from search_plan_tasks or get_plan_task in this "
            "conversation; never guess one or call propose_create_plan_task instead "
            "just because you don't have the id yet. To change who is assigned, use "
            "propose_assign_user_to_plan_task / propose_unassign_plan_task instead."
        ),
        parameters=_obj(
            {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "requirements": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": [
                        "strategy",
                        "creative",
                        "ads",
                        "content",
                        "analytics",
                        "compliance",
                        "admin",
                    ],
                },
                "status": {"type": "string", "enum": ["todo", "in_progress", "blocked", "done"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "archived": {"type": "boolean"},
            },
            required=["task_id"],
        ),
        kind="write",
        handler=handlers.propose_update_plan_task,
        progress_label="Drafting task changes",
    ),
    ToolSpec(
        name="propose_assign_user_to_plan_task",
        description=(
            "Assign a user (a user id from search_users) to an EXISTING plan task (a "
            "task_id from search_plan_tasks/get_plan_task — never call "
            "propose_create_plan_task for this, even if you don't have the id yet; "
            "search for it first)."
        ),
        parameters=_obj(
            {"task_id": {"type": "string"}, "assignee_id": {"type": "string"}},
            required=["task_id", "assignee_id"],
        ),
        kind="write",
        handler=handlers.propose_assign_user_to_plan_task,
        progress_label="Drafting the assignment",
    ),
    ToolSpec(
        name="propose_unassign_plan_task",
        description="Remove whoever is currently assigned to a plan task.",
        parameters=_obj({"task_id": {"type": "string"}}, required=["task_id"]),
        kind="write",
        handler=handlers.propose_unassign_plan_task,
        progress_label="Drafting the removal",
    ),
    ToolSpec(
        name="propose_archive_plan_task",
        description="Archive (soft-hide) a plan task from the board without deleting it.",
        parameters=_obj({"task_id": {"type": "string"}}, required=["task_id"]),
        kind="write",
        handler=handlers.propose_archive_plan_task,
        progress_label="Drafting the archive",
    ),
    ToolSpec(
        name="propose_delete_plan_task",
        description=(
            "Permanently delete a plan task. This cannot be undone. Only use this when "
            "the user clearly asked to delete (not archive/hide) the task."
        ),
        parameters=_obj({"task_id": {"type": "string"}}, required=["task_id"]),
        kind="write",
        handler=handlers.propose_delete_plan_task,
        progress_label="Drafting the delete",
    ),
    ToolSpec(
        name="propose_duplicate_plan_task",
        description="Create a fresh, unassigned, todo copy of an existing plan task.",
        parameters=_obj({"task_id": {"type": "string"}}, required=["task_id"]),
        kind="write",
        handler=handlers.propose_duplicate_plan_task,
        progress_label="Drafting a duplicate",
    ),
    ToolSpec(
        name="propose_add_plan_task_note",
        description="Add a comment/note to a plan task.",
        parameters=_obj(
            {"task_id": {"type": "string"}, "body": {"type": "string"}},
            required=["task_id", "body"],
        ),
        kind="write",
        handler=handlers.propose_add_plan_task_note,
        progress_label="Drafting the note",
    ),
    ToolSpec(
        name="propose_bulk_update_plan_tasks",
        description=(
            "Change status, priority, category, and/or the archived flag on EVERY task "
            "that matches a scope, in one go — for requests like 'archive everything in "
            "September', 'mark this week's tasks done', or 'move all of Q3's tasks to "
            "high priority'. Each match becomes its own drafted change in the SAME "
            "proposal, so the user approves them all together — this is not one task at "
            "a time.\n\n"
            "Give the scope as EITHER an explicit task_ids list (e.g. from a prior "
            "search_plan_tasks call, when the user pointed at specific items) OR "
            "start_date+end_date (resolve the user's wording — a week, a month, several "
            "months, a year, an explicit range — into concrete dates yourself), "
            "optionally narrowed further with query/status/assignee_id/assigned_only — "
            "e.g. 'assigned to Priya' (assignee_id, resolved via search_users), 'assigned "
            "to someone'/'only assigned tasks' (assigned_only=true, no specific person "
            "named), or 'unassigned tasks' (search_plan_tasks first with assigned_only, "
            "then pass those task_ids here — there's no unassigned-only flag since 'not "
            "assigned to this person' vs 'assigned to nobody at all' would be ambiguous "
            "otherwise). Don't combine task_ids with a date range in the same call.\n\n"
            "This tool never rewrites title/description/requirements in bulk (those are "
            "per-item by nature) — only status/priority/category/archived. For several "
            "separate, non-contiguous periods ('September and November, not October'), "
            "call this once per period in the same turn; both batches still land in one "
            "proposal."
        ),
        parameters=_obj(
            {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit task ids to change, if you already resolved them.",
                },
                "start_date": {"type": "string", "description": "YYYY-MM-DD — range start."},
                "end_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD — range end (inclusive).",
                },
                "query": {
                    "type": "string",
                    "description": "Optional: only tasks whose title contains this, within the range.",
                },
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "blocked", "done"],
                    "description": "Optional: only tasks currently in this status.",
                },
                "assignee_id": {
                    "type": "string",
                    "description": "A user id from search_users — only tasks assigned to that person.",
                },
                "assigned_only": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "True narrows to tasks with ANY assignee ('assigned to someone', "
                        "no specific name). Leave both this and assignee_id unset to include "
                        "assigned and unassigned tasks alike."
                    ),
                },
                "include_archived": {"type": "boolean", "default": False},
                "new_status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "blocked", "done"],
                },
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "category": {
                    "type": "string",
                    "enum": [
                        "strategy",
                        "creative",
                        "ads",
                        "content",
                        "analytics",
                        "compliance",
                        "admin",
                    ],
                },
                "archived": {"type": "boolean"},
            }
        ),
        kind="write",
        handler=handlers.propose_bulk_update_plan_tasks,
        progress_label="Drafting bulk changes",
    ),
    ToolSpec(
        name="propose_bulk_delete_plan_tasks",
        description=(
            "Permanently delete EVERY task that matches a scope, in one go — for "
            "requests like 'delete the whole plan for September' or 'remove everything "
            "from Sept 10 to Sept 25'. This cannot be undone once approved. Each match "
            "becomes its own drafted deletion in the SAME proposal, so the user approves "
            "them all together — this is not one task at a time. Only use this for a "
            "genuine delete request; if the user said 'archive'/'hide' instead, use "
            "propose_bulk_update_plan_tasks with archived=true.\n\n"
            "Give the scope as EITHER an explicit task_ids list OR start_date+end_date "
            "(resolve the user's wording into concrete dates yourself), optionally "
            "narrowed with query/status/assignee_id/assigned_only (see "
            "propose_bulk_update_plan_tasks for exactly how those work — same fields, "
            "same meaning here). Consider running search_plan_tasks with the same scope "
            "first to confirm the count with the user before deleting, especially for a "
            "wide or vague scope ('everything', 'the whole month'). For several separate, "
            "non-contiguous periods, call this once per period in the same turn."
        ),
        parameters=_obj(
            {
                "task_ids": {"type": "array", "items": {"type": "string"}},
                "start_date": {"type": "string", "description": "YYYY-MM-DD — range start."},
                "end_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD — range end (inclusive).",
                },
                "query": {"type": "string"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "blocked", "done"]},
                "assignee_id": {
                    "type": "string",
                    "description": "A user id from search_users — only tasks assigned to that person.",
                },
                "assigned_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "True narrows to tasks with ANY assignee ('assigned to someone').",
                },
                "include_archived": {"type": "boolean", "default": False},
            }
        ),
        kind="write",
        handler=handlers.propose_bulk_delete_plan_tasks,
        progress_label="Drafting the bulk delete",
    ),
    # Deliberately NOT registered as tools, even though the underlying
    # ProposalService methods exist and are tested: propose_assign_user_to_client
    # / propose_set_client_capabilities / propose_unassign_user_from_client /
    # propose_update_user. Client access and user administration are handled in
    # the agency dashboard, outside any one client's context — this chat only
    # ever sees and changes data belonging to the single client it's scoped to
    # (see command_agent/system.txt). Re-registering them here would let this
    # per-client chat grant/revoke access to itself or manage global accounts,
    # which is exactly the boundary the client asked us to keep.
    ToolSpec(
        name="propose_update_client",
        description=(
            "Change one or more of this client's basic profile fields or status. "
            "Administrator privileges required. Call get_client first if you don't "
            "already know the current values."
        ),
        parameters=_obj(
            {
                "name": {"type": "string"},
                "business_type": {"type": "string"},
                "industry": {"type": "string"},
                "website": {"type": "string"},
                "location": {"type": "string"},
                "language": {"type": "string"},
                "timezone": {"type": "string", "description": "IANA zone, e.g. America/New_York"},
                "markets": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["draft", "active", "inactive", "paused", "onboarding", "archived"],
                },
            }
        ),
        kind="write",
        handler=handlers.propose_update_client,
        progress_label="Drafting client changes",
    ),
    ToolSpec(
        name="propose_update_brand",
        description=(
            "Change one or more of this client's brand fields (voice, colors, fonts, "
            "logo, brand description). Setting `colors` or `fonts` REPLACES the whole "
            "list, so call get_client first if you're only changing part of it. "
            "Administrator privileges required."
        ),
        parameters=_obj(
            {
                "about_brand": {"type": "string"},
                "brand_voice": {"type": "string"},
                "color_guidelines": {"type": "string"},
                "logo_url": {"type": "string"},
                "colors": {
                    "type": "array",
                    "description": "Replaces the full color list.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hex": {"type": "string", "description": "e.g. #0EA5E9"},
                            "label": {"type": "string"},
                        },
                        "required": ["hex"],
                    },
                },
                "fonts": {
                    "type": "array",
                    "description": "Replaces the full font list.",
                    "items": {"type": "string"},
                },
            }
        ),
        kind="write",
        handler=handlers.propose_update_brand,
        progress_label="Drafting brand changes",
    ),
    # ---- control ----------------------------------------------------------- #
    ToolSpec(
        name=REQUEST_CLARIFICATION,
        description=(
            "Ask the user a clarifying question instead of guessing. You MUST call this "
            "— rather than picking one — whenever a search returned more than one "
            "plausible match (e.g. more than one user named 'John'), or a detail "
            "required to safely propose a change is missing or ambiguous (which task, "
            "which date, which client)."
        ),
        parameters=_obj({"question": {"type": "string"}}, required=["question"]),
        kind="clarify",
        handler=None,
    ),
]

TOOLS: dict[str, ToolSpec] = {t.name: t for t in _TOOLS}


def progress_label_for(name: str) -> str:
    """The streaming turn endpoint's live "what I'm doing" label for a tool
    call — cosmetic only, never affects dispatch."""
    spec = TOOLS.get(name)
    return (spec.progress_label if spec and spec.progress_label else None) or "Working"


def openai_tool_definitions(*, include_write: bool = True) -> list[dict[str, Any]]:
    """The ``tools=`` payload for ``LLMClient.complete_with_tools``.

    ``include_write=False`` (a read-only user — see ``CommandAgent``) omits
    every ``kind="write"`` tool entirely, so the model is never even offered a
    mutating action to call — the primary enforcement layer for read-only
    access, not just a cosmetic filter (``CommandAgent._dispatch`` also
    re-checks this defensively, but a tool that was never offered can't be
    called through normal tool-calling in the first place)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in _TOOLS
        if include_write or t.kind != "write"
    ]
