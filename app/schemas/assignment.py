"""Client assignment schemas (admin assigns clients to users)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.user import UserRead


class AssignmentCreate(BaseModel):
    user_id: uuid.UUID


class AssignmentRead(BaseModel):
    client_id: uuid.UUID
    assigned_by: uuid.UUID | None = None
    created_at: datetime
    user: UserRead


class AssignmentListResponse(BaseModel):
    items: list[AssignmentRead]
    total: int
