"""Client-assignment endpoints (admin only).

Assigning a client to a user is what grants that non-admin access to it. An
assignment can scope the user's per-project capabilities on that client (BE-03).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import AdminUser, DbSession
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentListResponse,
    AssignmentRead,
    AssignmentUpdate,
)
from app.services.assignment_service import AssignmentService

router = APIRouter(prefix="/clients/{client_id}/assignments", tags=["assignments"])


@router.get("", response_model=AssignmentListResponse, summary="List a client's assignees")
def list_assignments(
    client_id: uuid.UUID, _admin: AdminUser, db: DbSession
) -> AssignmentListResponse:
    return AssignmentService(db).list_for_client(client_id)


@router.post(
    "",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a client to a user",
)
def create_assignment(
    client_id: uuid.UUID, data: AssignmentCreate, admin: AdminUser, db: DbSession
) -> AssignmentRead:
    service = AssignmentService(db)
    a = service.assign(
        client_id, data.user_id, assigned_by=admin.id, capabilities=data.capabilities
    )
    return service.to_read(a)


@router.patch(
    "/{user_id}",
    response_model=AssignmentRead,
    summary="Update an assignment's per-project capabilities",
)
def update_assignment(
    client_id: uuid.UUID,
    user_id: uuid.UUID,
    data: AssignmentUpdate,
    _admin: AdminUser,
    db: DbSession,
) -> AssignmentRead:
    service = AssignmentService(db)
    a = service.set_capabilities(client_id, user_id, data.capabilities)
    return service.to_read(a)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unassign a client from a user",
)
def delete_assignment(
    client_id: uuid.UUID, user_id: uuid.UUID, _admin: AdminUser, db: DbSession
) -> None:
    AssignmentService(db).unassign(client_id, user_id)
