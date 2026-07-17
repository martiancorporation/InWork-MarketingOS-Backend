"""Alert schemas: KPI watchdog signals + acknowledge/resolve workflow."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import AlertKind, AlertSeverity, AlertStatus
from app.schemas.common import ORMModel


class AlertRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    campaign_id: uuid.UUID | None = None
    kind: AlertKind
    severity: AlertSeverity
    status: AlertStatus
    title: str
    detail: str | None = None
    metric: str | None = None
    threshold: float | None = None
    actual: float | None = None
    rec_key: str | None = None
    acknowledged_by: uuid.UUID | None = None
    resolved_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class AlertListResponse(BaseModel):
    items: list[AlertRead]
    total: int
    page: int = 1
    page_size: int = 20


class AlertEvaluateResult(BaseModel):
    """Outcome of a KPI evaluation pass over the client's campaigns."""

    evaluated_campaigns: int
    opened: int
    updated: int
    auto_resolved: int
    alerts: list[AlertRead]
