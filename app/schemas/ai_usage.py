"""AI usage read + analytics schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class AiUsageEventRead(ORMModel):
    id: uuid.UUID
    created_at: datetime
    actor_user_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    feature: str
    provider: str
    model: str
    operation: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    total_tokens: int
    input_cost: float
    output_cost: float
    cache_cost: float
    total_cost: float
    currency: str
    priced: bool
    status: str
    error: str | None = None
    duration_ms: int | None = None
    request_id: str | None = None


class AiUsageListResponse(BaseModel):
    items: list[AiUsageEventRead]
    total: int
    page: int
    page_size: int


class UsageTotals(BaseModel):
    requests: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    total_tokens: int
    total_cost: float
    currency: str = "USD"


class UsageGroupRow(BaseModel):
    key: str | None  # feature / model / client_id / user_id (as string)
    requests: int
    total_tokens: int
    total_cost: float


class DailyUsage(BaseModel):
    day: str  # ISO date
    requests: int
    total_tokens: int
    total_cost: float


class PlatformUsageSummary(BaseModel):
    totals: UsageTotals
    by_feature: list[UsageGroupRow]
    by_model: list[UsageGroupRow]
    by_client: list[UsageGroupRow]
    by_user: list[UsageGroupRow]
    daily: list[DailyUsage]


class ClientUsageSummary(BaseModel):
    client_id: uuid.UUID
    totals: UsageTotals
    by_feature: list[UsageGroupRow]
    by_model: list[UsageGroupRow]
    daily: list[DailyUsage]


# ---- cost optimization ---- #


class CostSuggestion(BaseModel):
    """A concrete way to reduce AI spend, with an estimated monthly saving."""

    id: str  # stable key, e.g. "route-cheaper-model:onboarding.brand_extraction"
    title: str
    detail: str
    feature: str | None = None  # the feature the suggestion targets, if specific
    current_model: str | None = None
    suggested_model: str | None = None
    # Estimated USD saving over the analyzed window if the suggestion is applied.
    estimated_savings: float = 0.0
    # Share of analyzed cost this suggestion could remove (0-100).
    savings_pct: int = 0
    confidence: int = 0  # 0-100


class CostOptimizationReport(BaseModel):
    analyzed_requests: int
    analyzed_cost: float
    currency: str = "USD"
    # Sum of estimated savings across suggestions (a ceiling, not a guarantee).
    potential_savings: float = 0.0
    suggestions: list[CostSuggestion] = []
