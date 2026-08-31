"""Analytics Breakdown schemas — dimensional slices for GA4/Search Console
(top pages, channels, devices, search queries). See
``app/models/analytics_breakdown.py`` for why this is a separate concept from
Platform Insights (no campaign/ad hierarchy here)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class AnalyticsBreakdownRead(ORMModel):
    id: uuid.UUID
    integration_key: str
    breakdown_type: str
    dimension: str
    rank: int
    metrics: dict
    created_at: datetime
    updated_at: datetime


class AnalyticsBreakdownListResponse(BaseModel):
    items: list[AnalyticsBreakdownRead]
    total: int
    page: int = 1
    page_size: int = 20
