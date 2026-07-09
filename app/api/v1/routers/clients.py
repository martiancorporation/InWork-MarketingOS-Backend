"""Client endpoints: list, onboarding, AI brand extraction, detail."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.ai.brand_extraction import BrandExtractionService
from app.api.deps import CurrentUser, DbSession, Pagination
from app.models.enums import ClientStatus
from app.schemas.client import ClientListResponse, ClientRead
from app.schemas.onboarding import (
    BrandExtraction,
    BrandExtractionRequest,
    OnboardingRequest,
    OnboardingResponse,
)
from app.services.client_service import ClientService
from app.services.onboarding_service import OnboardingService
from app.services.readiness_service import ReadinessService

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=ClientListResponse, summary="List clients")
def list_clients(
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    search: str | None = Query(None, description="Match name or industry"),
    status: ClientStatus | None = Query(None, description="Filter by status"),
) -> ClientListResponse:
    return ClientService(db).list_clients(
        user, pagination=pagination, search=search, status=status
    )


@router.post(
    "/onboarding",
    response_model=OnboardingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Onboard a new client",
)
def onboard_client(
    data: OnboardingRequest, user: CurrentUser, db: DbSession
) -> OnboardingResponse:
    client = OnboardingService(db).onboard(user, data)
    readiness = ReadinessService().report(client)
    return OnboardingResponse(
        client=ClientRead.model_validate(client), readiness=readiness
    )


@router.post(
    "/onboarding/extract-brand",
    response_model=BrandExtraction,
    summary="AI-extract a brand theme from a website or document",
)
async def extract_brand(
    data: BrandExtractionRequest, _user: CurrentUser
) -> BrandExtraction:
    return await BrandExtractionService().extract(data)


@router.get("/{client_id}", response_model=ClientRead, summary="Get a client")
def get_client(client_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ClientRead:
    client = ClientService(db).get_client(user, client_id)
    return ClientRead.model_validate(client)
