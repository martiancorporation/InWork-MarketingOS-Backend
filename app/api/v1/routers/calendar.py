"""Marketing-calendar API (v1) — content events + client-approval workflow.

- ``GET    /clients/{id}/calendar/events``                  — list (month grid / drafts)
- ``POST   /clients/{id}/calendar/events``                  — create a post/ad
- ``GET    /clients/{id}/calendar/events/{event_id}``       — full event detail
- ``PATCH  /clients/{id}/calendar/events/{event_id}``       — partial edit / reschedule
- ``POST   /clients/{id}/calendar/events/{event_id}/approval`` — approval transition
- ``DELETE /clients/{id}/calendar/events/{event_id}``       — remove

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404, never revealing its
existence. Any user who can see the client may manage its calendar — day-to-day
content work is exactly what assigned managers/users do.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination
from app.models.enums import ApprovalStatus, EventStage, EventType, SocialPlatform
from app.schemas.common import MessageResponse
from app.schemas.event import (
    ApprovalDecision,
    EventCreate,
    EventListResponse,
    EventRead,
    EventUpdate,
)
from app.services.calendar_service import CalendarService
from app.services.client_service import ClientService

router = APIRouter(prefix="/clients/{client_id}/calendar", tags=["calendar"])


@router.get("/events", response_model=EventListResponse, summary="List calendar events")
def list_events(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    year: int | None = Query(
        None, ge=1970, le=9999, description="Filter to a month (with `month`)"
    ),
    month: int | None = Query(None, ge=1, le=12, description="1-12; pair with `year`"),
    stage: EventStage | None = Query(None, description="draft / scheduled / published / archived"),
    platform: SocialPlatform | None = Query(None),
    type: EventType | None = Query(None),
    approval_status: ApprovalStatus | None = Query(None),
) -> EventListResponse:
    ClientService(db).get_client(user, client_id)  # 404 if not accessible
    return CalendarService(db).list_events(
        client_id,
        pagination=pagination,
        year=year,
        month=month,
        stage=stage,
        platform=platform,
        type=type,
        approval_status=approval_status,
    )


@router.post(
    "/events",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a calendar event (post or ad)",
)
def create_event(
    client_id: uuid.UUID, data: EventCreate, user: CurrentUser, db: DbSession
) -> EventRead:
    ClientService(db).get_client(user, client_id)
    event = CalendarService(db).create_event(client_id, data, created_by=user.id)
    return EventRead.model_validate(event)


@router.get("/events/{event_id}", response_model=EventRead, summary="Get a calendar event")
def get_event(
    client_id: uuid.UUID, event_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> EventRead:
    ClientService(db).get_client(user, client_id)
    event = CalendarService(db).get_event(client_id, event_id)
    return EventRead.model_validate(event)


@router.patch("/events/{event_id}", response_model=EventRead, summary="Edit / reschedule an event")
def update_event(
    client_id: uuid.UUID,
    event_id: uuid.UUID,
    data: EventUpdate,
    user: CurrentUser,
    db: DbSession,
) -> EventRead:
    ClientService(db).get_client(user, client_id)
    event = CalendarService(db).update_event(client_id, event_id, data, actor_id=user.id)
    return EventRead.model_validate(event)


@router.post(
    "/events/{event_id}/approval",
    response_model=EventRead,
    summary="Record a client-approval decision",
)
def decide_approval(
    client_id: uuid.UUID,
    event_id: uuid.UUID,
    data: ApprovalDecision,
    user: CurrentUser,
    db: DbSession,
) -> EventRead:
    ClientService(db).get_client(user, client_id)
    event = CalendarService(db).decide_approval(client_id, event_id, data, actor_id=user.id)
    return EventRead.model_validate(event)


@router.delete(
    "/events/{event_id}",
    response_model=MessageResponse,
    summary="Delete a calendar event",
)
def delete_event(
    client_id: uuid.UUID, event_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageResponse:
    ClientService(db).get_client(user, client_id)
    CalendarService(db).delete_event(client_id, event_id)
    return MessageResponse(detail="Event deleted.")
