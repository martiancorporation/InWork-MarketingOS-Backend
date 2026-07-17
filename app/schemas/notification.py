"""Notification schemas — the current user's notification centre."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import NotificationLevel
from app.schemas.common import ORMModel


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
