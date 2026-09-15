"""Unified Meta/Google Ads dashboard schema.

One call replaces "list `/integrations` then call per-provider
`platform-insights` separately" — the exact centralized paid-media monitoring
hub the client asked for.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import IntegrationStatus


class PlatformAdsOverview(BaseModel):
    platform: str
    status: IntegrationStatus | None = None
    campaign_count: int
    active_campaign_count: int
    spend_usd: float
    budget_usd: float | None = None
    remaining_usd: float | None = None


class AdsOverviewResponse(BaseModel):
    period: str
    combined_spend_usd: float
    combined_budget_usd: float | None = None
    combined_remaining_usd: float | None = None
    platforms: list[PlatformAdsOverview]
