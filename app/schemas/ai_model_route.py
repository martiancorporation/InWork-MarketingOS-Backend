"""Admin AI model-routing schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel, StrictModel

_MAX_NOTES = 500


class AiModelRouteRead(ORMModel):
    id: uuid.UUID
    task_category: str
    model_id: str
    fallback_model_id: str | None = None
    is_active: bool
    notes: str | None = None
    updated_by: uuid.UUID | None = None
    updated_at: datetime
    created_at: datetime


class AiModelRouteListResponse(BaseModel):
    """Every known task category, one row each (small, fixed set — no pagination,
    same stance as IntegrationListResponse)."""

    items: list[AiModelRouteRead]


class AiModelRouteUpdate(StrictModel):
    model_id: str = Field(min_length=1, max_length=80)
    fallback_model_id: str | None = Field(default=None, max_length=80)
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=_MAX_NOTES)


class AvailableModel(BaseModel):
    model_id: str
    label: str
    input_per_1m: float
    output_per_1m: float


class AvailableModelsResponse(BaseModel):
    items: list[AvailableModel]
