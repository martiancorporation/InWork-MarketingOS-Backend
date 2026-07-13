"""Client-intelligence API schemas (profile, directives, status, context)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ORMModel


class DirectiveRead(ORMModel):
    id: uuid.UUID
    type: str
    category: str
    text: str
    tier: str
    rank: int
    confidence: float
    status: str
    capability_flags: dict[str, Any] | None = None
    source_id: uuid.UUID | None = None
    conflicts_with_id: uuid.UUID | None = None


class ClientProfileRead(ORMModel):
    id: uuid.UUID
    version: int
    status: str
    summary_md: str | None = None
    profile: dict[str, Any] | None = None
    capability_flags: dict[str, Any] | None = None
    created_at: datetime


class IntelligenceResponse(BaseModel):
    status: str  # none | building | ready | failed
    version: int | None = None
    profile: ClientProfileRead | None = None
    directives: list[DirectiveRead] = []


class IntelligenceStatus(BaseModel):
    status: str  # none | building | ready | failed
    version: int | None = None
    job_status: str | None = None  # latest job state, if any
    updated_at: datetime | None = None


class ProfileVersionItem(BaseModel):
    version: int
    status: str
    created_at: datetime


class RetrievedChunk(BaseModel):
    text: str
    source_label: str | None = None
    score: float


class ClientContextResponse(BaseModel):
    version: int | None = None
    preamble: str
    capability_flags: dict[str, Any] = {}
    directives: list[DirectiveRead] = []
    retrieved: list[RetrievedChunk] = []
