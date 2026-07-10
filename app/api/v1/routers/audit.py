"""Audit-log API (admin only): query the immutable trail of who did what."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession, Pagination
from app.schemas.audit import AuditLogListResponse
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditLogListResponse, summary="List audit-log entries (admin)")
def list_audit(
    _admin: AdminUser,
    db: DbSession,
    pagination: Pagination,
    action: str | None = Query(None, description="Substring match on the action"),
    entity: str | None = Query(None, description="Exact entity, e.g. clients / users"),
    actor_user_id: uuid.UUID | None = Query(None, description="Filter by the acting user"),
    client_id: uuid.UUID | None = Query(None, description="Filter by client"),
) -> AuditLogListResponse:
    return AuditService(db).list(
        pagination,
        action=action,
        entity=entity,
        actor_user_id=actor_user_id,
        client_id=client_id,
    )
