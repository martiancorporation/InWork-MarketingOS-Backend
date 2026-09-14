"""Notifications API (v1) — the current user's notification centre.

- ``GET  /notifications``               — list my notifications (unread filter, paginated)
- ``GET  /notifications/unread-count``   — badge count for the "red dot"
- ``POST /notifications/read-all``       — mark all mine read
- ``POST /notifications/{id}/read``      — mark one read
- ``GET  /notifications/preferences``    — my email/mute preferences
- ``PUT  /notifications/preferences``    — update them

Every route is scoped to the authenticated user — a user only ever sees their
own notifications. No admin/client scoping applies.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession, Pagination
from app.schemas.common import MessageResponse
from app.schemas.notification import (
    NotificationListResponse,
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
    UnreadCount,
)
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse, summary="List my notifications")
def list_notifications(
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    unread_only: bool = Query(False),
) -> NotificationListResponse:
    return NotificationService(db).list_for_user(
        user.id, pagination=pagination, unread_only=unread_only
    )


@router.get("/unread-count", response_model=UnreadCount, summary="My unread count")
def unread_count(user: CurrentUser, db: DbSession) -> UnreadCount:
    return UnreadCount(unread=NotificationService(db).unread_count(user.id))


@router.post("/read-all", response_model=MessageResponse, summary="Mark all read")
def mark_all_read(user: CurrentUser, db: DbSession) -> MessageResponse:
    n = NotificationService(db).mark_all_read(user.id)
    return MessageResponse(detail=f"Marked {n} notification(s) read.")


@router.post("/{notification_id}/read", response_model=NotificationRead, summary="Mark one read")
def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> NotificationRead:
    return NotificationRead.model_validate(
        NotificationService(db).mark_read(user.id, notification_id)
    )


@router.get(
    "/preferences",
    response_model=NotificationPreferenceRead,
    summary="My notification email/mute preferences",
)
def get_preferences(user: CurrentUser, db: DbSession) -> NotificationPreferenceRead:
    return NotificationPreferenceRead.model_validate(
        NotificationService(db).get_preferences(user.id)
    )


@router.put(
    "/preferences",
    response_model=NotificationPreferenceRead,
    summary="Update my notification email/mute preferences",
)
def set_preferences(
    data: NotificationPreferenceUpdate, user: CurrentUser, db: DbSession
) -> NotificationPreferenceRead:
    return NotificationPreferenceRead.model_validate(
        NotificationService(db).set_preferences(user.id, data)
    )
