"""Dashboard AI API (v1) — health score, executive brief, watchdog, recommendations.

- ``GET  /clients/{id}/dashboard``                          — the full AI dashboard bundle
- ``POST /clients/{id}/recommendations/{rec_key}/decision`` — accept/modify/reject a rec
- ``GET  /clients/{id}/recommendations/decisions``          — decision history (audit trail)

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404. The dashboard leans on the
client's intelligence context (directive preamble) and, when Claude is
unconfigured, falls back to deterministic output grounded in real client data.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import CurrentUser, DbSession, require_capability
from app.core.rate_limit import RateLimit
from app.models.client import Client
from app.models.enums import ClientCapability
from app.schemas.ai import (
    DashboardResponse,
    OpportunityResponse,
    RecommendationActionListResponse,
    RecommendationActionRead,
    RecommendationDecisionRequest,
    SetupStatusResponse,
)
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/clients/{client_id}", tags=["dashboard"])


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Full AI dashboard (health, brief, watchdog, recommendations)",
    dependencies=[Depends(RateLimit("ai_dashboard", times=30, seconds=60))],
)
async def get_dashboard(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> DashboardResponse:
    client = ClientService(db).get_client(user, client_id)
    return await DashboardService(db).build(client, user_id=user.id)


@router.get(
    "/opportunities",
    response_model=OpportunityResponse,
    summary="AI growth opportunities (new markets/keywords) with external research",
    dependencies=[Depends(RateLimit("ai_opportunities", times=20, seconds=60))],
)
async def get_opportunities(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> OpportunityResponse:
    client = ClientService(db).get_client(user, client_id)
    return await DashboardService(db).opportunities(client, user_id=user.id)


@router.get(
    "/setup",
    response_model=SetupStatusResponse,
    summary="Per-client outstanding-setup items + count (red-dot indicator)",
)
def get_setup_status(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> SetupStatusResponse:
    client = ClientService(db).get_client(user, client_id)
    return DashboardService(db).setup_status(client)


@router.post(
    "/recommendations/{rec_key}/decision",
    response_model=RecommendationActionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an accept/modify/reject decision on a recommendation",
)
def decide_recommendation(
    client_id: uuid.UUID,
    data: RecommendationDecisionRequest,
    user: CurrentUser,
    db: DbSession,
    # Deciding on a recommendation is a "review results" responsibility (BE-03).
    _client: Annotated[
        Client, Depends(require_capability(ClientCapability.review_results))
    ],
    rec_key: str = Path(max_length=80, description="Stable recommendation id"),
) -> RecommendationActionRead:
    action = DashboardService(db).record_decision(
        client_id, rec_key, data, decided_by=user.id
    )
    return RecommendationActionRead.model_validate(action)


@router.get(
    "/recommendations/decisions",
    response_model=RecommendationActionListResponse,
    summary="Recommendation decision history",
)
def list_recommendation_decisions(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> RecommendationActionListResponse:
    ClientService(db).get_client(user, client_id)
    return DashboardService(db).list_decisions(client_id)
