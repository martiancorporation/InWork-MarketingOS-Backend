"""Campaign schemas: definition + KPI targets + actual rollups, with derived
metrics, an A/B comparison shape, and a target-relative health score.

Derived metrics (ctr/cpl/conversion_rate/roas) are computed from the stored
raw counters so they can never drift from the source numbers. The health score
is deliberately *goal-relative* (actual vs. the agreed targets), which is the
project-level campaign-health concept — not a platform score.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, computed_field, model_validator

from app.models.enums import AdObjective, CampaignStatus
from app.schemas.common import MAX_TEXT, ORMModel


def _rate(numerator: float, denominator: float) -> float | None:
    """Percentage numerator/denominator, or None when undefined (div-by-zero)."""
    return round(numerator / denominator * 100, 2) if denominator else None


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 2) if denominator else None


# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: AdObjective = AdObjective.awareness
    status: CampaignStatus = CampaignStatus.draft
    start_date: date | None = None
    end_date: date | None = None
    budget_usd: float = Field(0, ge=0)
    notes: str | None = Field(None, max_length=MAX_TEXT)
    target_cpl: float | None = Field(None, ge=0)
    target_ctr: float | None = Field(None, ge=0)  # percent, e.g. 2.5
    target_conversion_rate: float | None = Field(None, ge=0)  # percent

    @model_validator(mode="after")
    def _dates_ordered(self) -> CampaignCreate:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class CampaignUpdate(BaseModel):
    """Partial autosave — only fields present in the body apply (model_fields_set).

    Covers both the definition/targets and the actual rollup counters (the
    latter are what an integration or manual ingest would push)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    objective: AdObjective | None = None
    status: CampaignStatus | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget_usd: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=MAX_TEXT)
    target_cpl: float | None = Field(default=None, ge=0)
    target_ctr: float | None = Field(default=None, ge=0)
    target_conversion_rate: float | None = Field(default=None, ge=0)
    # actual rollups
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)
    leads: int | None = Field(default=None, ge=0)
    spend: float | None = Field(default=None, ge=0)
    revenue: float | None = Field(default=None, ge=0)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


class _CampaignMetrics(ORMModel):
    """Shared raw counters + derived metrics (base for read & compare rows)."""

    impressions: int
    clicks: int
    conversions: int
    leads: int
    spend: float
    revenue: float
    budget_usd: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ctr(self) -> float | None:
        return _rate(self.clicks, self.impressions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cpl(self) -> float | None:
        return _ratio(self.spend, self.leads)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def conversion_rate(self) -> float | None:
        return _rate(self.conversions, self.clicks)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def roas(self) -> float | None:
        return _ratio(self.revenue, self.spend)


class CampaignRead(_CampaignMetrics):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    objective: AdObjective
    status: CampaignStatus
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = None
    target_cpl: float | None = None
    target_ctr: float | None = None
    target_conversion_rate: float | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class CampaignListItem(_CampaignMetrics):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    objective: AdObjective
    status: CampaignStatus
    start_date: date | None = None
    end_date: date | None = None


class CampaignListResponse(BaseModel):
    items: list[CampaignListItem]
    total: int
    page: int = 1
    page_size: int = 20


# --------------------------------------------------------------------------- #
# A/B comparison
# --------------------------------------------------------------------------- #


class CampaignCompareRow(_CampaignMetrics):
    id: uuid.UUID
    name: str
    status: CampaignStatus


class CampaignCompareResponse(BaseModel):
    rows: list[CampaignCompareRow]
    # metric -> winning campaign id (higher-is-better for ctr/conversion_rate/roas/leads;
    # lower-is-better for cpl). Absent metric = no comparable data.
    winners: dict[str, uuid.UUID] = {}


# --------------------------------------------------------------------------- #
# Health (target-relative)
# --------------------------------------------------------------------------- #


class HealthDriver(BaseModel):
    label: str
    delta: float  # signed contribution / gap vs target (percent points or ratio)


class CampaignHealth(BaseModel):
    campaign_id: uuid.UUID
    score: int = Field(ge=0, le=100)
    band: str  # excellent | good | attention | critical (matches web thresholds)
    drivers: list[HealthDriver] = []
    summary: str
    has_targets: bool
    ai_generated: bool = False
