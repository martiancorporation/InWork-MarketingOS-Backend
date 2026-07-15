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

from fastapi import APIRouter, Path, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.ai import (
    DashboardResponse,
    RecommendationActionListResponse,
    RecommendationActionRead,
    RecommendationDecisionRequest,
)
from app.services.client_service import ClientService
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/clients/{client_id}", tags=["dashboard"])


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Full AI dashboard (health, brief, watchdog, recommendations)",
)
async def get_dashboard(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> DashboardResponse:
    client = ClientService(db).get_client(user, client_id)
    return await DashboardService(db).build(client, user_id=user.id)


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
    rec_key: str = Path(max_length=80, description="Stable recommendation id"),
) -> RecommendationActionRead:
    ClientService(db).get_client(user, client_id)
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
