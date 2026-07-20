"""Dashboard AI schemas: health score, executive brief, watchdog, recommendations.

These mirror the web dashboard's card data. Field names are snake_case (the
backend convention); the frontend maps them to its camelCase view types.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import RecommendationDecision
from app.schemas.common import ORMModel

HealthBand = Literal["excellent", "good", "attention", "critical"]
Tone = Literal["up", "down", "flat"]
Pace = Literal["on-track", "ahead", "behind"]
Severity = Literal["low", "medium", "high"]
RecCategory = Literal["budget", "creative", "audience", "compliance", "growth"]
WatchdogKind = Literal["alert", "opportunity"]


# ---- health score ---- #


class HealthDriver(BaseModel):
    label: str
    delta: int  # signed points contribution (+/-)


class HealthScore(BaseModel):
    score: int = Field(ge=0, le=100)
    band: HealthBand
    drivers: list[HealthDriver] = []


# ---- executive brief ---- #


class BriefMetric(BaseModel):
    label: str
    value: str
    delta: str
    tone: Tone


class Budget(BaseModel):
    spent: float
    total: float
    pace: Pace


class CampaignNote(BaseModel):
    name: str
    note: str


class ExecutiveBrief(BaseModel):
    headline: str
    metrics: list[BriefMetric] = []
    budget: Budget
    top_campaign: CampaignNote
    worst_campaign: CampaignNote
    pending_actions: list[str] = []


# ---- watchdog ---- #


class WatchdogItem(BaseModel):
    id: str
    kind: WatchdogKind
    title: str
    detail: str
    severity: Severity


# ---- recommendations ---- #


class RecommendationDecisionRead(ORMModel):
    decision: RecommendationDecision
    reason: str | None = None
    decided_by: uuid.UUID | None = None
    created_at: datetime


class RecProjection(BaseModel):
    """The expected effect of acting on a recommendation.

    Deliberately allows a *qualitative* estimate (e.g. ``"+10–15%"`` or
    ``"materially lower"``) so it stays honest when there isn't enough data to
    model an exact number — ``basis`` says what the estimate is grounded in.
    """

    metric: str  # e.g. "CTR", "traffic", "CPL", "leads", "attribution coverage"
    direction: str  # "up" | "down"
    estimate: str  # the projected change (may be a range or qualitative)
    basis: str  # why — the evidence the estimate rests on


class Recommendation(BaseModel):
    id: str  # stable rec_key, e.g. "rec-connect-integrations"
    title: str
    category: RecCategory
    severity: Severity
    summary: str
    reason: str
    confidence: int = Field(ge=0, le=100)
    expected_impact: str
    # Optional structured projection of the expected impact (traffic/CTR/CPL …),
    # so the UI can show "why + what to expect". Estimates, not guarantees.
    projection: RecProjection | None = None
    # The latest human decision on this recommendation, if any (merged in on read).
    decision: RecommendationDecisionRead | None = None


class DashboardResponse(BaseModel):
    """Everything the client dashboard needs in one call."""

    health_score: HealthScore
    executive_brief: ExecutiveBrief
    watchdog: list[WatchdogItem] = []
    recommendations: list[Recommendation] = []
    # False when Claude is unconfigured/failed and the deterministic fallback ran.
    ai_generated: bool


# ---- recommendation decision (write) ---- #


class RecommendationDecisionRequest(BaseModel):
    decision: RecommendationDecision
    reason: str | None = Field(None, max_length=2000)


class RecommendationActionRead(ORMModel):
    id: uuid.UUID
    rec_key: str
    decision: RecommendationDecision
    reason: str | None = None
    decided_by: uuid.UUID | None = None
    created_at: datetime


class RecommendationActionListResponse(BaseModel):
    items: list[RecommendationActionRead]
    total: int


# ---- per-client outstanding-setup indicator (BE-05) ---- #

SetupItemKey = Literal[
    "onboarding_incomplete",
    "no_integrations",
    "no_intelligence_profile",
    "pending_approvals",
]


class SetupItem(BaseModel):
    key: SetupItemKey
    label: str
    detail: str


class SetupStatusResponse(BaseModel):
    """The red-dot data: outstanding setup items for a client + their count.

    ``complete`` is True when nothing is outstanding (``count == 0``). Reuses the
    dashboard signals so the indicator never drifts from the dashboard itself.
    """

    client_id: uuid.UUID
    complete: bool
    count: int
    items: list[SetupItem] = []
