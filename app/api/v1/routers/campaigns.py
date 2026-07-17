"""Campaigns API (v1) — multi-campaign management, A/B comparison, health.

- ``GET    /clients/{id}/campaigns``                 — list (status filter)
- ``POST   /clients/{id}/campaigns``                 — create
- ``GET    /clients/{id}/campaigns/compare?ids=..``  — A/B comparison (2+ ids)
- ``GET    /clients/{id}/campaigns/{cid}``           — detail (+ derived metrics)
- ``GET    /clients/{id}/campaigns/{cid}/health``    — target-relative health score
- ``PATCH  /clients/{id}/campaigns/{cid}``           — edit definition / targets / actuals
- ``DELETE /clients/{id}/campaigns/{cid}``           — remove

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404. The ``/compare`` route is
declared before ``/{campaign_id}`` so the literal segment wins the match.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination
from app.models.enums import CampaignStatus
from app.schemas.campaign import (
    CampaignCompareResponse,
    CampaignCreate,
    CampaignHealth,
    CampaignListResponse,
    CampaignRead,
    CampaignUpdate,
)
from app.schemas.common import MessageResponse
from app.services.campaign_service import CampaignService
from app.services.client_service import ClientService

router = APIRouter(prefix="/clients/{client_id}/campaigns", tags=["campaigns"])


@router.get("", response_model=CampaignListResponse, summary="List campaigns")
def list_campaigns(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    status_filter: CampaignStatus | None = Query(None, alias="status"),
) -> CampaignListResponse:
    ClientService(db).get_client(user, client_id)
    return CampaignService(db).list_campaigns(
        client_id,
        pagination=pagination,
        status=status_filter.value if status_filter else None,
    )


@router.post(
    "",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign",
)
def create_campaign(
    client_id: uuid.UUID, data: CampaignCreate, user: CurrentUser, db: DbSession
) -> CampaignRead:
    ClientService(db).get_client(user, client_id)
    campaign = CampaignService(db).create_campaign(client_id, data, created_by=user.id)
    return CampaignRead.model_validate(campaign)


@router.get(
    "/compare",
    response_model=CampaignCompareResponse,
    summary="Compare campaigns side by side (A/B)",
)
def compare_campaigns(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    ids: list[uuid.UUID] = Query(..., min_length=2, description="Campaign ids to compare"),
) -> CampaignCompareResponse:
    ClientService(db).get_client(user, client_id)
    return CampaignService(db).compare(client_id, ids)


@router.get(
    "/{campaign_id}", response_model=CampaignRead, summary="Get a campaign"
)
def get_campaign(
    client_id: uuid.UUID, campaign_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> CampaignRead:
    ClientService(db).get_client(user, client_id)
    return CampaignRead.model_validate(
        CampaignService(db).get_campaign(client_id, campaign_id)
    )


@router.get(
    "/{campaign_id}/health",
    response_model=CampaignHealth,
    summary="Target-relative campaign health score",
)
def campaign_health(
    client_id: uuid.UUID, campaign_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> CampaignHealth:
    ClientService(db).get_client(user, client_id)
    return CampaignService(db).health(client_id, campaign_id)


@router.patch(
    "/{campaign_id}", response_model=CampaignRead, summary="Update a campaign"
)
def update_campaign(
    client_id: uuid.UUID,
    campaign_id: uuid.UUID,
    data: CampaignUpdate,
    user: CurrentUser,
    db: DbSession,
) -> CampaignRead:
    ClientService(db).get_client(user, client_id)
    return CampaignRead.model_validate(
        CampaignService(db).update_campaign(client_id, campaign_id, data)
    )


@router.delete(
    "/{campaign_id}", response_model=MessageResponse, summary="Delete a campaign"
)
def delete_campaign(
    client_id: uuid.UUID, campaign_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageResponse:
    ClientService(db).get_client(user, client_id)
    CampaignService(db).delete_campaign(client_id, campaign_id)
    return MessageResponse(detail="Campaign deleted.")
