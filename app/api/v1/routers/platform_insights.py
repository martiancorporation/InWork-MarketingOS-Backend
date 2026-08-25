"""Platform Insights API (v1) — the "see every campaign" surface.

- ``GET  /clients/{id}/platform-insights/{key}/campaigns``                 — list
- ``GET  /clients/{id}/platform-insights/{key}/campaigns/{campaign_id}``   — detail (ad sets + ads)
- ``GET  /clients/{id}/platform-insights/{key}/campaigns/{campaign_id}/metrics`` — daily series
- ``GET  /clients/{id}/platform-insights/{key}/recommendations``          — platform-native recs
- ``POST /clients/{id}/platform-insights/{key}/recommendations/{rec_id}/dismiss``
- ``GET  /clients/{id}/platform-insights/{key}/delivery-issues``          — derived issues
- ``POST /clients/{id}/platform-insights/{key}/delivery-issues/{issue_id}/resolve``

Named "recommendations"/"delivery issues" to stay visibly distinct from the
AI-generated Watchdog/Recommendations and the KPI-threshold Alerts module —
these are the *platform's own* (e.g. Meta's) native signals.

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404, never revealing its
existence.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Pagination, RequireClient
from app.models.enums import IntegrationKey
from app.schemas.platform_insight import (
    PlatformCampaignDetailRead,
    PlatformCampaignListResponse,
    PlatformDeliveryIssueListResponse,
    PlatformDeliveryIssueRead,
    PlatformMetricSeriesResponse,
    PlatformRecommendationListResponse,
    PlatformRecommendationRead,
)
from app.services.platform_insight_service import PlatformInsightService

router = APIRouter(prefix="/clients/{client_id}/platform-insights", tags=["platform-insights"])


@router.get(
    "/{key}/campaigns", response_model=PlatformCampaignListResponse, summary="List campaigns"
)
def list_campaigns(
    client_id: uuid.UUID,
    key: IntegrationKey,
    db: DbSession,
    pagination: Pagination,
    _client: RequireClient,
    status_filter: str | None = Query(None, alias="status"),
) -> PlatformCampaignListResponse:
    return PlatformInsightService(db).list_campaigns(
        client_id, key.value, pagination=pagination, status=status_filter
    )


@router.get(
    "/{key}/campaigns/{campaign_id}",
    response_model=PlatformCampaignDetailRead,
    summary="Get a campaign (with its ad sets and ads)",
)
def get_campaign(
    client_id: uuid.UUID,
    key: IntegrationKey,
    campaign_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
) -> PlatformCampaignDetailRead:
    campaign = PlatformInsightService(db).get_campaign(client_id, key.value, campaign_id)
    return PlatformCampaignDetailRead.model_validate(campaign)


@router.get(
    "/{key}/campaigns/{campaign_id}/metrics",
    response_model=PlatformMetricSeriesResponse,
    summary="Daily metrics series for one campaign",
)
def campaign_metrics(
    client_id: uuid.UUID,
    key: IntegrationKey,
    campaign_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    start: date | None = None,
    end: date | None = None,
) -> PlatformMetricSeriesResponse:
    return PlatformInsightService(db).campaign_metrics(
        client_id, key.value, campaign_id, start=start, end=end
    )


@router.get(
    "/{key}/recommendations",
    response_model=PlatformRecommendationListResponse,
    summary="List the platform's own recommendations",
)
def list_recommendations(
    client_id: uuid.UUID,
    key: IntegrationKey,
    db: DbSession,
    pagination: Pagination,
    _client: RequireClient,
    status_filter: str | None = Query(None, alias="status"),
) -> PlatformRecommendationListResponse:
    return PlatformInsightService(db).list_recommendations(
        client_id, key.value, pagination=pagination, status=status_filter
    )


@router.post(
    "/{key}/recommendations/{rec_id}/dismiss",
    response_model=PlatformRecommendationRead,
    summary="Dismiss a platform recommendation (local-only; the platform has no ack API)",
)
def dismiss_recommendation(
    client_id: uuid.UUID,
    key: IntegrationKey,
    rec_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
) -> PlatformRecommendationRead:
    rec = PlatformInsightService(db).dismiss_recommendation(client_id, rec_id)
    return PlatformRecommendationRead.model_validate(rec)


@router.get(
    "/{key}/delivery-issues",
    response_model=PlatformDeliveryIssueListResponse,
    summary="List derived delivery issues (status divergence, disapproval, ...)",
)
def list_delivery_issues(
    client_id: uuid.UUID,
    key: IntegrationKey,
    db: DbSession,
    pagination: Pagination,
    _client: RequireClient,
    status_filter: str | None = Query(None, alias="status"),
) -> PlatformDeliveryIssueListResponse:
    return PlatformInsightService(db).list_delivery_issues(
        client_id, key.value, pagination=pagination, status=status_filter
    )


@router.post(
    "/{key}/delivery-issues/{issue_id}/resolve",
    response_model=PlatformDeliveryIssueRead,
    summary="Mark a delivery issue resolved",
)
def resolve_delivery_issue(
    client_id: uuid.UUID,
    key: IntegrationKey,
    issue_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
) -> PlatformDeliveryIssueRead:
    issue = PlatformInsightService(db).resolve_delivery_issue(client_id, issue_id)
    return PlatformDeliveryIssueRead.model_validate(issue)
