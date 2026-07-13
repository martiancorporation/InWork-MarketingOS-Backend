"""Client endpoints: list, onboarding (atomic + step-by-step), AI brand
extraction, detail.

The onboarding surface mirrors the web wizard. ``POST /onboarding`` still
accepts the whole payload atomically, but the wizard drives the *progressive*
endpoints: ``POST /onboarding/draft`` opens a draft at the mandatory step 1,
``PATCH /{id}/onboarding`` autosaves each later step, ``POST /{id}/documents``
attaches uploads, and ``POST /{id}/onboarding/complete`` finalizes. Every
progressive call returns the recomputed readiness score and wizard progress so
the UI's meters stay honest.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.ai.brand_extraction import BrandExtractionService
from app.ai.features import AiFeature
from app.ai.usage import AiUsageContext
from app.api.deps import AdminUser, CurrentUser, DbSession, Pagination
from app.models.client import Client
from app.models.enums import ClientStatus
from app.schemas.client import ClientListResponse, ClientRead
from app.schemas.onboarding import (
    BrandExtraction,
    BrandExtractionRequest,
    DocumentsRequest,
    OnboardingDraftRequest,
    OnboardingRequest,
    OnboardingResponse,
    OnboardingStepResponse,
    OnboardingStepUpdate,
)
from app.services.client_service import ClientService
from app.services.intelligence.intelligence_service import IntelligenceService
from app.services.onboarding_service import OnboardingService
from app.services.readiness_service import ReadinessService

router = APIRouter(prefix="/clients", tags=["clients"])


def _step_response(client: Client, db) -> OnboardingStepResponse:
    """Bundle a client with its readiness, wizard progress, and build status."""
    return OnboardingStepResponse(
        client=ClientRead.model_validate(client),
        readiness=ReadinessService().report(client),
        onboarding=OnboardingService.progress(client),
        intelligence=IntelligenceService(db).status(client),
    )


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
    summary="Onboard a new client (admin only)",
)
def onboard_client(
    data: OnboardingRequest, admin: AdminUser, db: DbSession
) -> OnboardingResponse:
    client = OnboardingService(db).onboard(admin, data)
    readiness = ReadinessService().report(client)
    return OnboardingResponse(
        client=ClientRead.model_validate(client),
        readiness=readiness,
        intelligence=IntelligenceService(db).status(client),
    )


@router.post(
    "/onboarding/draft",
    response_model=OnboardingStepResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start onboarding — create the draft client (step 1, admin only)",
)
def start_onboarding(
    data: OnboardingDraftRequest, admin: AdminUser, db: DbSession
) -> OnboardingStepResponse:
    client = OnboardingService(db).create_draft(admin, data)
    return _step_response(client, db)


@router.patch(
    "/{client_id}/onboarding",
    response_model=OnboardingStepResponse,
    summary="Autosave an onboarding step (admin only)",
)
def update_onboarding_step(
    client_id: uuid.UUID,
    data: OnboardingStepUpdate,
    admin: AdminUser,
    db: DbSession,
) -> OnboardingStepResponse:
    service = OnboardingService(db)
    client = service.get(client_id)
    client = service.update_step(admin, client, data)
    return _step_response(client, db)


@router.post(
    "/{client_id}/documents",
    response_model=OnboardingStepResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach uploaded documents to a client (admin only)",
)
def attach_documents(
    client_id: uuid.UUID,
    data: DocumentsRequest,
    admin: AdminUser,
    db: DbSession,
) -> OnboardingStepResponse:
    service = OnboardingService(db)
    client = service.get(client_id)
    client = service.add_documents(admin, client, data.documents)
    return _step_response(client, db)


@router.post(
    "/{client_id}/onboarding/complete",
    response_model=OnboardingStepResponse,
    summary="Finalize onboarding (step 8, admin only)",
)
def complete_onboarding(
    client_id: uuid.UUID, admin: AdminUser, db: DbSession
) -> OnboardingStepResponse:
    service = OnboardingService(db)
    client = service.get(client_id)
    client = service.complete(client)
    return _step_response(client, db)


@router.post(
    "/onboarding/extract-brand",
    response_model=BrandExtraction,
    summary="AI-extract a brand theme from a website or document",
)
async def extract_brand(
    data: BrandExtractionRequest, user: CurrentUser
) -> BrandExtraction:
    context = AiUsageContext(
        feature=AiFeature.BRAND_EXTRACTION,
        user_id=user.id,
        meta={"website": data.website},
    )
    return await BrandExtractionService().extract(data, context)


@router.get("/{client_id}", response_model=ClientRead, summary="Get a client")
def get_client(client_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ClientRead:
    client = ClientService(db).get_client(user, client_id)
    return ClientRead.model_validate(client)
