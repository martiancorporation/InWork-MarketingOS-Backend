"""Analytics API (v1) — daily performance facts + aggregated summaries.

- ``POST /clients/{id}/analytics/ingest``  — upsert daily rows (integration/manual)
- ``GET  /clients/{id}/analytics/daily``   — raw daily rows (time series)
- ``GET  /clients/{id}/analytics/summary`` — totals + by-platform + daily series

Client-access-scoped via ``ClientService.get_client`` (admin or assigned user);
an inaccessible client returns 404.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.models.enums import SocialPlatform
from app.schemas.analytics import (
    AnalyticsDailyListResponse,
    AnalyticsIngestRequest,
    AnalyticsIngestResponse,
    AnalyticsSummary,
)
from app.services.analytics_service import AnalyticsService
from app.services.client_service import ClientService

router = APIRouter(prefix="/clients/{client_id}/analytics", tags=["analytics"])


@router.post("/ingest", response_model=AnalyticsIngestResponse, summary="Upsert daily analytics rows")
def ingest(
    client_id: uuid.UUID, data: AnalyticsIngestRequest, user: CurrentUser, db: DbSession
) -> AnalyticsIngestResponse:
    ClientService(db).get_client(user, client_id)
    upserted = AnalyticsService(db).ingest(client_id, data.rows)
    return AnalyticsIngestResponse(upserted=upserted)


@router.get("/daily", response_model=AnalyticsDailyListResponse, summary="Raw daily analytics rows")
def list_daily(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = Query(None, description="Inclusive start date"),
    end: date | None = Query(None, description="Inclusive end date"),
    platform: SocialPlatform | None = Query(None),
) -> AnalyticsDailyListResponse:
    ClientService(db).get_client(user, client_id)
    return AnalyticsService(db).list_daily(client_id, start=start, end=end, platform=platform)


@router.get("/summary", response_model=AnalyticsSummary, summary="Aggregated analytics summary")
def summary(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = Query(None, description="Inclusive start date"),
    end: date | None = Query(None, description="Inclusive end date"),
    platform: SocialPlatform | None = Query(None),
) -> AnalyticsSummary:
    ClientService(db).get_client(user, client_id)
    return AnalyticsService(db).summary(client_id, start=start, end=end, platform=platform)
