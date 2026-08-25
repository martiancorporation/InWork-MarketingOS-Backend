"""Platform Insights schemas: campaigns → ad sets → ads, daily metrics,
platform-native recommendations, and derived delivery issues."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class PlatformAdRead(ORMModel):
    id: uuid.UUID
    external_id: str
    name: str
    status: str | None = None
    effective_status: str | None = None
    creative_summary: dict | None = None
    issues_info: dict | list | None = None
    ad_review_feedback: dict | list | None = None
    created_at: datetime
    updated_at: datetime


class PlatformAdSetRead(ORMModel):
    id: uuid.UUID
    external_id: str
    name: str
    status: str | None = None
    effective_status: str | None = None
    daily_budget: float | None = None
    lifetime_budget: float | None = None
    targeting_summary: dict | None = None
    ads: list[PlatformAdRead] = []
    created_at: datetime
    updated_at: datetime


class PlatformCampaignRead(ORMModel):
    id: uuid.UUID
    integration_key: str
    external_id: str
    name: str
    objective: str | None = None
    status: str | None = None
    effective_status: str | None = None
    daily_budget: float | None = None
    lifetime_budget: float | None = None
    start_time: datetime | None = None
    stop_time: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PlatformCampaignDetailRead(PlatformCampaignRead):
    ad_sets: list[PlatformAdSetRead] = []


class PlatformCampaignListResponse(BaseModel):
    items: list[PlatformCampaignRead]
    total: int
    page: int = 1
    page_size: int = 20


class PlatformMetricDailyRead(ORMModel):
    date: date
    impressions: int
    clicks: int
    spend: float
    reach: int
    frequency: float
    cpm: float
    cpc: float
    conversions: int
    revenue: float


class PlatformMetricSeriesResponse(BaseModel):
    entity_type: str
    entity_id: str
    items: list[PlatformMetricDailyRead]


class PlatformRecommendationRead(ORMModel):
    id: uuid.UUID
    entity_type: str
    entity_id: str
    code: str | None = None
    title: str
    message: str | None = None
    importance: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class PlatformRecommendationListResponse(BaseModel):
    items: list[PlatformRecommendationRead]
    total: int
    page: int = 1
    page_size: int = 20


class PlatformDeliveryIssueRead(ORMModel):
    id: uuid.UUID
    entity_type: str
    entity_id: str
    severity: str
    reason: str
    detail: dict | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class PlatformDeliveryIssueListResponse(BaseModel):
    items: list[PlatformDeliveryIssueRead]
    total: int
    page: int = 1
    page_size: int = 20
