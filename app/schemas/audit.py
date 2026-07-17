"""Audit-log read schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel


class AuditLogRead(ORMModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    entity: str
    entity_id: uuid.UUID | None = None
    action: str
    target_label: str | None = None
    meta: dict | None = None
    changes: dict | None = None  # {field: {before, after}} — what actually changed
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogRead]
    total: int
    page: int
    page_size: int
