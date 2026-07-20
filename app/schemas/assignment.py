"""Client assignment schemas (admin assigns clients to users).

An assignment can grant a subset of ``ClientCapability`` values (granular
per-project RBAC, BE-03). Omitting ``capabilities`` on create grants the full
set, preserving the pre-RBAC behaviour.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ClientCapability
from app.schemas.common import StrictModel
from app.schemas.user import UserRead

# Small, closed set — an assignment can't list more capabilities than exist.
_MAX_CAPS = len(ClientCapability)


class AssignmentCreate(StrictModel):
    user_id: uuid.UUID
    # None → full capability set (backward compatible). A list scopes the grant.
    capabilities: list[ClientCapability] | None = Field(
        default=None, max_length=_MAX_CAPS
    )


class AssignmentUpdate(StrictModel):
    """Replace the capability set on an existing assignment."""

    capabilities: list[ClientCapability] = Field(max_length=_MAX_CAPS)


class AssignmentRead(BaseModel):
    client_id: uuid.UUID
    assigned_by: uuid.UUID | None = None
    created_at: datetime
    # The effective capability set for this assignment (a legacy NULL set is
    # returned as the full list, so the client always sees a concrete set).
    capabilities: list[ClientCapability]
    user: UserRead


class AssignmentListResponse(BaseModel):
    items: list[AssignmentRead]
    total: int
