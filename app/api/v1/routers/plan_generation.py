"""AI content-calendar generation API (v1) — the client's top-requested
feature: chat → generate a month of draft content → manager reviews & assigns
→ tasks land on the Plan board + calendar.

- ``POST /clients/{id}/plan/ai/propose``                    — generate a month of drafts
- ``POST /clients/{id}/plan/ai/items/{task_id}/assign``     — approve + assign (any authorized user)
- ``POST /clients/{id}/plan/ai/items/{task_id}/reject``     — cancel with a reason (admin only)
- ``POST /clients/{id}/plan/ai/items/{task_id}/regenerate`` — real-time single-day edit (admin only)

Every route is client-access-scoped; an inaccessible client returns 404, never
revealing its existence. Generation/regeneration call the AI provider, so both
are rate-limited like every other paid-AI route.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, CurrentUser, DbSession, RequireClient, require_capability
from app.core.rate_limit import RateLimit
from app.models.client import Client
from app.models.enums import ClientCapability
from app.schemas.plan_generation import (
    GeneratedPlanTaskRead,
    PlanGenerationAssign,
    PlanGenerationPropose,
    PlanGenerationProposeResponse,
    PlanGenerationRegenerate,
    PlanGenerationReject,
)
from app.services.plan_generation_service import PlanGenerationService

router = APIRouter(prefix="/clients/{client_id}/plan/ai", tags=["plan-generation"])


@router.post(
    "/propose",
    response_model=PlanGenerationProposeResponse,
    summary="Generate a month of draft content-calendar items",
    dependencies=[Depends(RateLimit("plan_generation", times=20, seconds=60))],
)
async def propose(
    client_id: uuid.UUID,
    data: PlanGenerationPropose,
    user: CurrentUser,
    db: DbSession,
    # Generating calendar content is a "manage calendar" responsibility (BE-03).
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_calendar))],
) -> PlanGenerationProposeResponse:
    items = await PlanGenerationService(db).propose_month(
        client_id, data.prompt, data.month, user=user
    )
    return PlanGenerationProposeResponse(items=items)


@router.post(
    "/items/{task_id}/assign",
    response_model=GeneratedPlanTaskRead,
    summary="Approve a generated item and assign it to a team member",
)
def assign(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    data: PlanGenerationAssign,
    user: CurrentUser,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_calendar))],
) -> GeneratedPlanTaskRead:
    return PlanGenerationService(db).assign(client_id, task_id, data, actor=user)


@router.post(
    "/items/{task_id}/reject",
    response_model=GeneratedPlanTaskRead,
    summary="Cancel a generated item with a reason (admin only)",
)
def reject(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    data: PlanGenerationReject,
    admin: AdminUser,
    db: DbSession,
    _client: RequireClient,
) -> GeneratedPlanTaskRead:
    return PlanGenerationService(db).reject(client_id, task_id, data, actor=admin)


@router.post(
    "/items/{task_id}/regenerate",
    response_model=GeneratedPlanTaskRead,
    summary="Real-time single-day regeneration per a client change request (admin only)",
    dependencies=[Depends(RateLimit("plan_generation", times=20, seconds=60))],
)
async def regenerate(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    data: PlanGenerationRegenerate,
    admin: AdminUser,
    db: DbSession,
    _client: RequireClient,
) -> GeneratedPlanTaskRead:
    return await PlanGenerationService(db).regenerate_item(client_id, task_id, data, actor=admin)
