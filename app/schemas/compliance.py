"""Compliance-register schemas.

Mirrors the web compliance page: additive entries (brand_voice / banned /
required / rule / note) whose active set is the effective ruleset the AI enforces.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ComplianceKind
from app.schemas.common import ORMModel


class ComplianceEntryCreate(BaseModel):
    kind: ComplianceKind
    text: str = Field(min_length=1)


class ComplianceEntryUpdate(BaseModel):
    kind: ComplianceKind | None = None
    text: str | None = Field(None, min_length=1)
    is_active: bool | None = None


class ComplianceEntryRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    kind: ComplianceKind
    text: str
    author_id: uuid.UUID | None = None
    is_active: bool
    created_at: datetime


class ComplianceListResponse(BaseModel):
    items: list[ComplianceEntryRead]
    total: int
