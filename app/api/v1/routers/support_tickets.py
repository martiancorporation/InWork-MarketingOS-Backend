"""Support & feedback ticket API.

Global, owner-scoped (no ``client_id`` prefix) — a user files and manages
their own tickets; an admin sees and can act on every ticket. See
``app/services/support_ticket_service.py`` for the authorization rules.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, StorageDep
from app.core.rate_limit import RateLimit
from app.models.enums import TicketCategory, TicketPriority, TicketStatus
from app.schemas.common import MessageResponse
from app.schemas.support_ticket import (
    SupportTicketCreate,
    SupportTicketListResponse,
    SupportTicketRead,
    SupportTicketUpdate,
)
from app.services.support_ticket_service import SupportTicketService

router = APIRouter(prefix="/support-tickets", tags=["support-tickets"])


@router.post(
    "",
    response_model=SupportTicketRead,
    status_code=status.HTTP_201_CREATED,
    summary="File a new support/feedback ticket",
    dependencies=[Depends(RateLimit("support_ticket_create", times=20, seconds=60))],
)
def create_ticket(
    data: SupportTicketCreate, user: CurrentUser, db: DbSession, storage: StorageDep
) -> SupportTicketRead:
    return SupportTicketService(db, storage).create_ticket(user, data)


@router.get(
    "",
    response_model=SupportTicketListResponse,
    summary="List tickets (own tickets for a user, every ticket for an admin)",
)
def list_tickets(
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    pagination: Pagination,
    search: str | None = Query(default=None, max_length=200),
    status_: TicketStatus | None = Query(default=None, alias="status"),
    category: TicketCategory | None = None,
    priority: TicketPriority | None = None,
) -> SupportTicketListResponse:
    return SupportTicketService(db, storage).list_tickets(
        user,
        pagination=pagination,
        search=search,
        status=status_,
        category=category,
        priority=priority,
    )


@router.get(
    "/{ticket_id}",
    response_model=SupportTicketRead,
    summary="Get a ticket",
)
def get_ticket(
    ticket_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> SupportTicketRead:
    return SupportTicketService(db, storage).get_ticket(user, ticket_id)


@router.put(
    "/{ticket_id}",
    response_model=SupportTicketRead,
    summary="Update a ticket (partial — only fields present in the body are applied)",
)
def update_ticket(
    ticket_id: uuid.UUID,
    data: SupportTicketUpdate,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
) -> SupportTicketRead:
    return SupportTicketService(db, storage).update_ticket(user, ticket_id, data)


@router.delete(
    "/{ticket_id}",
    response_model=MessageResponse,
    summary="Delete a ticket",
)
def delete_ticket(
    ticket_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> MessageResponse:
    SupportTicketService(db, storage).delete_ticket(user, ticket_id)
    return MessageResponse(detail="Support ticket deleted.")
