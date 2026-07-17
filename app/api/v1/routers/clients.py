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

from anyio import to_thread
from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.ai.brand_extraction import BrandExtractionService
from app.ai.features import AiFeature
from app.ai.usage import AiUsageContext
from app.api.deps import AdminUser, CurrentUser, DbSession, Pagination, StorageDep
from app.core.rate_limit import RateLimit
from app.models.client import Client
from app.models.enums import ClientStatus
from app.schemas.brand_job import BrandJobRead
from app.schemas.client import ClientListResponse, ClientRead, ClientUpdate
from app.schemas.consistency import ConsistencyReport
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
from app.services.brand_job_service import BrandJobService
from app.services.client_service import ClientService
from app.services.intelligence.intelligence_service import IntelligenceService
from app.services.onboarding_service import OnboardingService
from app.services.readiness_service import ReadinessService
from app.services.upload_service import UploadService

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
    "/{client_id}/onboarding/consistency",
    response_model=ConsistencyReport,
    summary="AI cross-field consistency check for the review step (admin only)",
)
async def check_onboarding_consistency(
    client_id: uuid.UUID, admin: AdminUser, db: DbSession
) -> ConsistencyReport:
    return await OnboardingService(db).consistency(client_id)


@router.post(
    "/onboarding/extract-brand",
    response_model=BrandExtraction,
    summary="AI-extract a brand theme from a website or document",
    dependencies=[Depends(RateLimit("extract_brand", times=10, seconds=60))],
)
async def extract_brand(
    data: BrandExtractionRequest, user: CurrentUser, db: DbSession, storage: StorageDep
) -> BrandExtraction:
    document = None
    if data.document_upload_id is not None:
        # Owner-scoped fetch (404 if not the caller's) then parse off the event loop.
        raw, content_type, filename = await to_thread.run_sync(
            UploadService(db, storage).read_bytes, user, data.document_upload_id
        )
        document = await to_thread.run_sync(
            BrandExtractionService.document_from_bytes, raw, content_type, filename
        )
    context = AiUsageContext(
        feature=AiFeature.BRAND_EXTRACTION,
        user_id=user.id,
        meta={"website": data.website, "document_upload_id": str(data.document_upload_id or "")},
    )
    return await BrandExtractionService().extract(data, context, document=document)


@router.post(
    "/onboarding/extract-brand/jobs",
    response_model=BrandJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an async brand extraction (returns a transaction id to poll)",
    dependencies=[Depends(RateLimit("extract_brand", times=10, seconds=60))],
)
def start_brand_job(
    data: BrandExtractionRequest,
    user: CurrentUser,
    db: DbSession,
    background: BackgroundTasks,
) -> BrandJobRead:
    # For long scrapes/parses (>25s): create the job, return its id immediately,
    # process in the background; the client polls the GET endpoint for the result.
    job = BrandJobService(db).create(user, data)
    background.add_task(BrandJobService.process, job.id)
    return BrandJobRead.model_validate(job)


@router.get(
    "/onboarding/extract-brand/jobs/{job_id}",
    response_model=BrandJobRead,
    summary="Poll an async brand-extraction job",
)
def get_brand_job(job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> BrandJobRead:
    return BrandJobRead.model_validate(BrandJobService(db).get(user, job_id))


@router.get("/{client_id}", response_model=ClientRead, summary="Get a client")
def get_client(client_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ClientRead:
    client = ClientService(db).get_client(user, client_id)
    return ClientRead.model_validate(client)


@router.patch(
    "/{client_id}",
    response_model=ClientRead,
    summary="Update a client's status or basic profile (admin only)",
)
def update_client(
    client_id: uuid.UUID, data: ClientUpdate, _admin: AdminUser, db: DbSession
) -> ClientRead:
    client = ClientService(db).update_client(client_id, data)
    return ClientRead.model_validate(client)
