"""Strategy + adherence schemas (BE-06).

A strategy is the AI-given plan the operator signs off on (versioned per client).
Adherence measures — deterministically — how much the operator actually followed
it, from recommendation decisions and plan-task completion.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import MAX_TEXT, ORMModel, StrictModel


class StrategyCreate(StrictModel):
    title: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=MAX_TEXT)


class StrategyRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    version: int
    title: str | None = None
    content: str
    signed_by: uuid.UUID | None = None
    created_at: datetime


class AdherenceSummary(BaseModel):
    """How closely the operator followed the recorded strategy.

    Ratios are 0..1; ``adherence_score`` is the 0..100 blend of whichever signals
    are available (``basis`` lists them). Everything is derived deterministically
    — no AI, no stored score.
    """

    client_id: uuid.UUID
    has_strategy: bool
    current_version: int | None = None

    total_recommendations: int
    accepted: int
    modified: int
    rejected: int
    decision_adherence: float | None = None  # (accepted + 0.5*modified) / total

    tasks_total: int
    tasks_done: int
    task_completion: float | None = None  # done / total

    adherence_score: int | None = None  # 0..100 blend of the available signals
    basis: list[str] = []
