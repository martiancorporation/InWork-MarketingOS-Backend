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
from functools import partial

from anyio import to_thread
from fastapi import APIRouter, File, Query, UploadFile

from app.api.deps import DbSession, Pagination, RequireClient
from app.models.enums import SocialPlatform
from app.schemas.analytics import (
    AnalyticsCsvImportResponse,
    AnalyticsDailyListResponse,
    AnalyticsIngestRequest,
    AnalyticsIngestResponse,
    AnalyticsSummary,
)
from app.services.analytics_service import AnalyticsService
from app.utils.streaming import read_capped

_CSV_MAX_BYTES = 5 * 1024 * 1024  # 5 MB cap on an uploaded CSV

router = APIRouter(prefix="/clients/{client_id}/analytics", tags=["analytics"])


@router.post(
    "/ingest", response_model=AnalyticsIngestResponse, summary="Upsert daily analytics rows"
)
def ingest(
    client_id: uuid.UUID, data: AnalyticsIngestRequest, db: DbSession, _client: RequireClient
) -> AnalyticsIngestResponse:
    upserted = AnalyticsService(db).ingest(client_id, data.rows)
    return AnalyticsIngestResponse(upserted=upserted)


@router.post(
    "/import",
    response_model=AnalyticsCsvImportResponse,
    summary="Import daily analytics from a CSV upload",
)
async def import_csv(
    client_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    file: UploadFile = File(
        ...,
        description="CSV with header: date,platform,impressions,clicks,conversions,leads,spend,revenue",
    ),
) -> AnalyticsCsvImportResponse:
    raw = await read_capped(file, _CSV_MAX_BYTES)
    # Parsing + upserting up to 5,000 rows is real DB work; keep it off the
    # event loop so it doesn't stall every other request on this worker.
    return await to_thread.run_sync(partial(AnalyticsService(db).import_csv, client_id, raw))


@router.get("/daily", response_model=AnalyticsDailyListResponse, summary="Raw daily analytics rows")
def list_daily(
    client_id: uuid.UUID,
    db: DbSession,
    pagination: Pagination,
    _client: RequireClient,
    start: date | None = Query(None, description="Inclusive start date"),
    end: date | None = Query(None, description="Inclusive end date"),
    platform: SocialPlatform | None = Query(None),
) -> AnalyticsDailyListResponse:
    return AnalyticsService(db).list_daily(
        client_id, pagination=pagination, start=start, end=end, platform=platform
    )


@router.get("/summary", response_model=AnalyticsSummary, summary="Aggregated analytics summary")
def summary(
    client_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    start: date | None = Query(None, description="Inclusive start date"),
    end: date | None = Query(None, description="Inclusive end date"),
    platform: SocialPlatform | None = Query(None),
) -> AnalyticsSummary:
    return AnalyticsService(db).summary(client_id, start=start, end=end, platform=platform)
