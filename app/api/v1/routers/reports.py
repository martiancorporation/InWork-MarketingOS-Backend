"""Reports API (v1) — a registry/history of generated client reports.

- ``GET    /clients/{id}/reports``              — list (optional kind filter)
- ``POST   /clients/{id}/reports``              — generate a report file + record it
- ``GET    /clients/{id}/reports/{report_id}``  — one report
- ``PATCH  /clients/{id}/reports/{report_id}``  — manually attach file / tweak delivery
- ``DELETE /clients/{id}/reports/{report_id}``  — delete

Client-access-scoped via ``ClientService.get_client`` (admin or assigned user);
an inaccessible client returns 404.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, StorageDep
from app.models.enums import ReportKind
from app.schemas.common import MessageResponse
from app.schemas.report import (
    ReportCreate,
    ReportListResponse,
    ReportRead,
    ReportUpdate,
)
from app.services.client_service import ClientService
from app.services.report_service import ReportService

router = APIRouter(prefix="/clients/{client_id}/reports", tags=["reports"])


@router.get("", response_model=ReportListResponse, summary="List generated reports")
def list_reports(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    kind: ReportKind | None = Query(None, description="Filter by report kind"),
) -> ReportListResponse:
    ClientService(db).get_client(user, client_id)
    return ReportService(db).list_reports(client_id, pagination=pagination, kind=kind)


@router.post(
    "",
    response_model=ReportRead,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a report file and record it",
)
async def create_report(
    client_id: uuid.UUID, data: ReportCreate, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ReportRead:
    client = ClientService(db).get_client(user, client_id)
    report = await ReportService(db).create_report(client, data, user=user, storage=storage)
    return ReportRead.model_validate(report)


@router.get("/{report_id}", response_model=ReportRead, summary="Get a report")
def get_report(
    client_id: uuid.UUID, report_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ReportRead:
    ClientService(db).get_client(user, client_id)
    report = ReportService(db).get_report(client_id, report_id)
    return ReportRead.model_validate(report)


@router.patch("/{report_id}", response_model=ReportRead, summary="Update a report")
def update_report(
    client_id: uuid.UUID,
    report_id: uuid.UUID,
    data: ReportUpdate,
    user: CurrentUser,
    db: DbSession,
) -> ReportRead:
    ClientService(db).get_client(user, client_id)
    report = ReportService(db).update_report(client_id, report_id, data)
    return ReportRead.model_validate(report)


@router.delete("/{report_id}", response_model=MessageResponse, summary="Delete a report")
def delete_report(
    client_id: uuid.UUID, report_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageResponse:
    ClientService(db).get_client(user, client_id)
    ReportService(db).delete_report(client_id, report_id)
    return MessageResponse(detail="Report deleted.")
