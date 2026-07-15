"""Compliance API (v1) — the per-client compliance register.

- ``GET    /clients/{id}/compliance``              — list entries (kind / active filters)
- ``POST   /clients/{id}/compliance``              — add an entry
- ``PATCH  /clients/{id}/compliance/{entry_id}``   — edit / (de)activate
- ``DELETE /clients/{id}/compliance/{entry_id}``   — delete
- ``POST   /clients/{id}/compliance/sync``         — force the ruleset into the AI now

Entry changes enqueue an intelligence rebuild (the effective ruleset feeds the
client's directives). Client-access-scoped via ``ClientService.get_client``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models.enums import ComplianceKind
from app.schemas.common import MessageResponse
from app.schemas.compliance import (
    ComplianceEntryCreate,
    ComplianceEntryRead,
    ComplianceEntryUpdate,
    ComplianceListResponse,
)
from app.schemas.intelligence import IntelligenceStatus
from app.services.client_service import ClientService
from app.services.compliance_service import ComplianceService

router = APIRouter(prefix="/clients/{client_id}/compliance", tags=["compliance"])


@router.get("", response_model=ComplianceListResponse, summary="List compliance entries")
def list_entries(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    kind: ComplianceKind | None = Query(None, description="Filter by entry kind"),
    active_only: bool = Query(False, description="Only active (effective) entries"),
) -> ComplianceListResponse:
    ClientService(db).get_client(user, client_id)
    return ComplianceService(db).list_entries(client_id, kind=kind, active_only=active_only)


@router.post(
    "",
    response_model=ComplianceEntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a compliance entry",
)
def create_entry(
    client_id: uuid.UUID, data: ComplianceEntryCreate, user: CurrentUser, db: DbSession
) -> ComplianceEntryRead:
    ClientService(db).get_client(user, client_id)
    entry = ComplianceService(db).create_entry(client_id, data, author_id=user.id)
    return ComplianceEntryRead.model_validate(entry)


@router.patch(
    "/{entry_id}", response_model=ComplianceEntryRead, summary="Edit or (de)activate an entry"
)
def update_entry(
    client_id: uuid.UUID,
    entry_id: uuid.UUID,
    data: ComplianceEntryUpdate,
    user: CurrentUser,
    db: DbSession,
) -> ComplianceEntryRead:
    ClientService(db).get_client(user, client_id)
    entry = ComplianceService(db).update_entry(client_id, entry_id, data)
    return ComplianceEntryRead.model_validate(entry)


@router.delete(
    "/{entry_id}", response_model=MessageResponse, summary="Delete a compliance entry"
)
def delete_entry(
    client_id: uuid.UUID, entry_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageResponse:
    ClientService(db).get_client(user, client_id)
    ComplianceService(db).delete_entry(client_id, entry_id)
    return MessageResponse(detail="Compliance entry deleted.")


@router.post(
    "/sync",
    response_model=IntelligenceStatus,
    summary="Force the effective ruleset into the AI (triggers a rebuild)",
)
def sync_ruleset(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> IntelligenceStatus:
    ClientService(db).get_client(user, client_id)
    return ComplianceService(db).sync(client_id)
