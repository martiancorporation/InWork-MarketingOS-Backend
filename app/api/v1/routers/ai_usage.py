"""AI usage & cost analytics API.

- ``GET /ai-usage``                    — detailed per-request log (admin)
- ``GET /ai-usage/summary``            — platform totals + breakdowns (admin)
- ``GET /ai-usage/clients/{id}/summary`` — one client's usage (admin or assigned)

All figures are read from ``ai_usage_events`` (written at AI call time).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession, Pagination, RequireClient
from app.repositories.ai_usage_repository import UsageFilters
from app.schemas.ai_usage import (
    AiUsageListResponse,
    ClientUsageSummary,
    CostOptimizationReport,
    PlatformUsageSummary,
)
from app.services.ai_usage_service import AiUsageService

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])


def _filters(
    client_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    feature: str | None,
    model: str | None,
    status: str | None,
    start: datetime | None,
    end: datetime | None,
) -> UsageFilters:
    return UsageFilters(
        client_id=client_id,
        user_id=user_id,
        feature=feature,
        model=model,
        status=status,
        start=start,
        end=end,
    )


@router.get("", response_model=AiUsageListResponse, summary="Detailed AI usage log (admin)")
def list_usage(
    _admin: AdminUser,
    db: DbSession,
    pagination: Pagination,
    client_id: uuid.UUID | None = Query(None),
    user_id: uuid.UUID | None = Query(
        None, description="Filter by the user who triggered the call"
    ),
    feature: str | None = Query(None, description="Origin, e.g. onboarding.brand_extraction"),
    model: str | None = Query(None),
    status: str | None = Query(None, description="success | error"),
    start: datetime | None = Query(None, description="ISO start of window (inclusive)"),
    end: datetime | None = Query(None, description="ISO end of window (inclusive)"),
) -> AiUsageListResponse:
    f = _filters(client_id, user_id, feature, model, status, start, end)
    return AiUsageService(db).list(f, pagination)


@router.get(
    "/summary",
    response_model=PlatformUsageSummary,
    summary="Platform usage & cost analytics (admin)",
)
def platform_summary(
    _admin: AdminUser,
    db: DbSession,
    client_id: uuid.UUID | None = Query(None),
    user_id: uuid.UUID | None = Query(None),
    feature: str | None = Query(None),
    model: str | None = Query(None),
    status: str | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
) -> PlatformUsageSummary:
    f = _filters(client_id, user_id, feature, model, status, start, end)
    return AiUsageService(db).platform_summary(f)


@router.get(
    "/optimization",
    response_model=CostOptimizationReport,
    summary="Platform-wide AI cost-optimization suggestions (admin)",
)
def platform_optimization(
    _admin: AdminUser,
    db: DbSession,
    client_id: uuid.UUID | None = Query(None),
    feature: str | None = Query(None),
    model: str | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
) -> CostOptimizationReport:
    f = UsageFilters(client_id=client_id, feature=feature, model=model, start=start, end=end)
    return AiUsageService(db).optimization(f)


@router.get(
    "/clients/{client_id}/optimization",
    response_model=CostOptimizationReport,
    summary="A single client's AI cost-optimization suggestions (admin or assigned user)",
)
def client_optimization(
    client_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
) -> CostOptimizationReport:
    f = UsageFilters(client_id=client_id, start=start, end=end)
    return AiUsageService(db).optimization(f)


@router.get(
    "/clients/{client_id}/summary",
    response_model=ClientUsageSummary,
    summary="A single client's AI usage & cost (admin or assigned user)",
)
def client_summary(
    client_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    feature: str | None = Query(None),
) -> ClientUsageSummary:
    f = UsageFilters(client_id=client_id, feature=feature, start=start, end=end)
    return AiUsageService(db).client_summary(f)
