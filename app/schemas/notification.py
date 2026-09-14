"""Notification schemas — the current user's notification centre + preferences."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import NotificationLevel
from app.schemas.common import ORMModel, StrictModel

#: A single user's mute list is small in practice; this just stops an
#: unbounded payload, matching the "bound all inputs" house rule.
MAX_MUTED_CLIENTS = 200


class NotificationRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID | None = None
    kind: str
    level: NotificationLevel
    title: str
    body: str | None = None
    link: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationRead]
    total: int
    unread: int
    page: int = 1
    page_size: int = 20


class UnreadCount(BaseModel):
    unread: int


class NotificationPreferenceRead(ORMModel):
    email_enabled: bool
    muted_client_ids: list[uuid.UUID]


class NotificationPreferenceUpdate(StrictModel):
    email_enabled: bool | None = None
    muted_client_ids: list[uuid.UUID] | None = Field(None, max_length=MAX_MUTED_CLIENTS)
