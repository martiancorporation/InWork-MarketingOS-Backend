"""Analytics schemas: daily performance facts + aggregated summaries.

``analytics_daily`` holds one row per (client, date, platform). Integrations (or
manual ingest) upsert rows; the summary/series endpoints aggregate them for the
dashboard KPIs and the analytics charts.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.enums import SocialPlatform
from app.schemas.common import ORMModel


class AnalyticsDailyIn(BaseModel):
    date: date
    platform: SocialPlatform
    impressions: int = Field(0, ge=0)
    clicks: int = Field(0, ge=0)
    conversions: int = Field(0, ge=0)
    leads: int = Field(0, ge=0)
    spend: float = Field(0, ge=0)
    revenue: float = Field(0, ge=0)


class AnalyticsIngestRequest(BaseModel):
    rows: list[AnalyticsDailyIn] = Field(min_length=1, max_length=1000)


class AnalyticsIngestResponse(BaseModel):
    upserted: int


class AnalyticsDailyRead(ORMModel):
    date: date
    platform: SocialPlatform
    impressions: int
    clicks: int
    conversions: int
    leads: int
    spend: float
    revenue: float


class AnalyticsDailyListResponse(BaseModel):
    items: list[AnalyticsDailyRead]
    total: int
    page: int = 1
    page_size: int = 20


class AnalyticsTotals(BaseModel):
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    leads: int = 0
    spend: float = 0.0
    revenue: float = 0.0
    # derived
    ctr: float = 0.0  # clicks / impressions (%)
    cpl: float = 0.0  # spend / leads
    roas: float = 0.0  # revenue / spend


class PlatformBreakdownRow(BaseModel):
    platform: SocialPlatform
    impressions: int
    clicks: int
    leads: int
    spend: float
    revenue: float


class DailySeriesRow(BaseModel):
    date: date
    impressions: int
    clicks: int
    leads: int
    spend: float
    revenue: float


class AnalyticsSummary(BaseModel):
    totals: AnalyticsTotals
    by_platform: list[PlatformBreakdownRow] = []
    daily: list[DailySeriesRow] = []
