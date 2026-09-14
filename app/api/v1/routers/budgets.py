"""Client budget API (v1) — combined + per-platform monthly ad-spend caps.

- ``GET /clients/{id}/budgets``  — all rows for a period (combined + per-platform)
- ``PUT /clients/{id}/budgets``  — upsert one row (combined or a named platform)

Every route is client-access-scoped via ``RequireClient``; an inaccessible
client returns 404, never revealing its existence. Setting a budget is a
"manage_campaigns" responsibility — the same capability that gates campaign
edits.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, RequireClient, require_capability
from app.models.client import Client
from app.models.enums import ClientCapability
from app.schemas.budget import BudgetPeriodResponse, BudgetRead, BudgetSet
from app.services.budget_service import BudgetService

router = APIRouter(prefix="/clients/{client_id}/budgets", tags=["budgets"])


@router.get("", response_model=BudgetPeriodResponse, summary="Get budgets for a period")
def get_period(
    client_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    period: str = Query(..., min_length=7, max_length=7, description="YYYY-MM"),
) -> BudgetPeriodResponse:
    return BudgetService(db).get_period(client_id, period)


@router.put("", response_model=BudgetRead, summary="Set (upsert) a budget for a period")
def set_budget(
    client_id: uuid.UUID,
    data: BudgetSet,
    user: CurrentUser,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_campaigns))],
) -> BudgetRead:
    row = BudgetService(db).set_budget(client_id, data, set_by=user.id)
    return BudgetRead.model_validate(row)
