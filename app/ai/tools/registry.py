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
            "Search this client's plan/task board. Use this to find the task(s) "
            "the user is referring to before proposing any change to one."
        ),
        parameters=_obj(
            {
                "query": {"type": "string", "description": "Substring match on the task title."},
                "status": {"type": "string", "enum": ["todo", "in_progress", "blocked", "done"]},
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
            "question about the client (\"what's our brand voice\", \"what are we not "
            "allowed to say\", \"what are this client's goals\") instead of guessing "
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
            "\"how are my ads doing\" / \"what's our spend\" style questions."
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
        description="Draft a new task on the plan/task board. Requires human approval before it exists.",
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
            "status, priority, dates, archived) on an existing task. To change who is "
            "assigned, use propose_assign_user_to_plan_task / propose_unassign_plan_task "
            "instead."
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
        description="Assign a user (a user id from search_users) to a plan task.",
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


def openai_tool_definitions() -> list[dict[str, Any]]:
    """The ``tools=`` payload for ``LLMClient.complete_with_tools``."""
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
    ]
