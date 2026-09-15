"""AI content-calendar generation schemas.

Backs the "manager asks in chat, reviews the draft, assigns it" workflow:
``propose`` creates draft tasks (+ linked calendar events) immediately so
nothing is lost on refresh; ``assign``/``reject``/``regenerate`` act on one
already-created draft item.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field, field_validator

from app.models.enums import TaskPriority
from app.schemas.common import MAX_TEXT, StrictModel, validate_period
from app.schemas.plan import PlanTaskRead


class PlanGenerationPropose(StrictModel):
    #: Optional — additional instructions on top of the client's existing
    #: brand voice, goals, and strategy. Blank is fine: the AI already has
    #: enough context to generate a sensible plan without a prompt.
    prompt: str = Field(default="", max_length=MAX_TEXT)
    #: Target month, "YYYY-MM"; defaults to the current month client-side if omitted.
    month: str = Field(min_length=7, max_length=7, description="YYYY-MM")

    @field_validator("month")
    @classmethod
    def _validate_month(cls, value: str) -> str:
        return validate_period(value)


class PlanGenerationAssign(StrictModel):
    assignee_id: uuid.UUID
    priority: TaskPriority | None = None
    due_date: date | None = None


class PlanGenerationReject(StrictModel):
    reason: str = Field(min_length=1, max_length=MAX_TEXT)


class PlanGenerationRegenerate(StrictModel):
    instructions: str = Field(min_length=1, max_length=MAX_TEXT)
    reason: str = Field(min_length=1, max_length=MAX_TEXT)


class PlanTaskContentRead(BaseModel):
    """The linked calendar item's content detail, for an AI-generated task."""

    event_id: uuid.UUID
    platform: str
    event_date: date
    approval_status: str
    stage: str
    caption: str | None = None
    hashtags: str | None = None
    content_format: str | None = None


class GeneratedPlanTaskRead(PlanTaskRead):
    """A ``PlanTaskRead`` enriched with its linked calendar item's content, when
    the task represents an AI-generated content post."""

    content: PlanTaskContentRead | None = None


class PlanGenerationProposeResponse(BaseModel):
    items: list[GeneratedPlanTaskRead]
