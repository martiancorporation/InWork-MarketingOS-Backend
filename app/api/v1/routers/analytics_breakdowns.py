"""Analytics Breakdowns API (v1) — GA4/Search Console dimensional slices
(top pages, channels, devices, search queries).

- ``GET /clients/{id}/analytics-breakdowns/{key}`` — list, optional ``?type=``

Distinct from Platform Insights (``platform_insights.py``): GA4 and Search
Console have no campaign/ad hierarchy, just ranked dimensions — see
``app/models/analytics_breakdown.py``.

Client-access-scoped via ``ClientService.get_client`` (admin or assigned
user); an inaccessible client returns 404, never revealing its existence.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Pagination, RequireClient
from app.models.enums import IntegrationKey
from app.schemas.analytics_breakdown import AnalyticsBreakdownListResponse
from app.services.analytics_breakdown_service import AnalyticsBreakdownService

router = APIRouter(
    prefix="/clients/{client_id}/analytics-breakdowns", tags=["analytics-breakdowns"]
)


@router.get(
    "/{key}",
    response_model=AnalyticsBreakdownListResponse,
    summary="List GA4/Search Console dimensional breakdowns",
)
def list_breakdowns(
    client_id: uuid.UUID,
    key: IntegrationKey,
    db: DbSession,
    pagination: Pagination,
    _client: RequireClient,
    breakdown_type: str | None = Query(None, alias="type"),
) -> AnalyticsBreakdownListResponse:
    return AnalyticsBreakdownService(db).list_breakdowns(
        client_id, key.value, pagination=pagination, breakdown_type=breakdown_type
    )
