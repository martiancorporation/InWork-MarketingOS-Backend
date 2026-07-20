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
QAStatus = Literal["ok", "concerns", "not_reviewed"]


# ---- cross-provider QA ---- #


class QAVerdict(BaseModel):
    """Independent verdict from the SECOND LLM provider on generated content.

    ``not_reviewed`` is the clean passthrough returned when QA is disabled or the
    QA provider is unconfigured — it is never an error, just "no second opinion".
    """

    status: QAStatus = "not_reviewed"
    # The provider that produced the review (e.g. "openai"); None when not reviewed.
    provider: str | None = None
    # The model that produced the review; None when not reviewed.
    model: str | None = None
    notes: list[str] = []
    summary: str | None = None


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
    # Independent second-provider review of the generated brief + recommendations.
    # ``status="not_reviewed"`` when QA is disabled/unconfigured (the default).
    qa_review: QAVerdict = Field(default_factory=QAVerdict)


# ---- opportunities (external research) ---- #

OpportunityKind = Literal["market", "location", "keyword", "channel", "audience", "other"]


class Opportunity(BaseModel):
    id: str  # stable key, e.g. "opp-expand-fl-metros"
    kind: OpportunityKind
    title: str
    detail: str
    rationale: str
    confidence: int = Field(ge=0, le=100)
    # Research URLs backing the opportunity (empty for internal-signal opportunities).
    sources: list[str] = []


class OpportunityResponse(BaseModel):
    items: list[Opportunity] = []
    # True when external research (Brave/ScrapingBee) contributed grounding.
    researched: bool
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
