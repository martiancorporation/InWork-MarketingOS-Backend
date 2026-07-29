"""Report schemas — a registry of generated client reports.

Mirrors the web report generator (date range, scope preset, channels, sections,
output format, Outlook-draft delivery). Creating a report generates a real file
server-side (see ``app/services/reports/``) and records the result — config +
the resulting permanent download link, not the rendered bytes themselves.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ReportFormat, ReportKind
from app.schemas.common import ORMModel


class ReportCreate(BaseModel):
    kind: ReportKind = ReportKind.performance
    format: ReportFormat = ReportFormat.pdf
    title: str = Field(min_length=1, max_length=200)
    date_from: date
    date_to: date
    scope: str | None = Field(None, max_length=40)  # holistic / leads-only / meta-only / custom
    channels: list[str] | None = None
    sections: list[str] | None = None
    save_to_outlook_draft: bool = False
    # Ignored on create — the service always generates a real file and sets
    # this itself. Kept only so an old client sending it doesn't 422; use
    # ReportUpdate to attach/replace a file manually after the fact.
    file_url: str | None = None

    @model_validator(mode="after")
    def _check_range(self) -> ReportCreate:
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        return self


class ReportUpdate(BaseModel):
    """Manually attach/replace the file, or tweak delivery, after the fact. Partial."""

    title: str | None = Field(None, min_length=1, max_length=200)
    file_url: str | None = None
    save_to_outlook_draft: bool | None = None


class ReportRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    kind: ReportKind
    format: ReportFormat
    title: str
    date_from: date
    date_to: date
    scope: str | None = None
    channels: list[str] | None = None
    sections: list[str] | None = None
    save_to_outlook_draft: bool
    file_url: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime


class ReportListResponse(BaseModel):
    items: list[ReportRead]
    total: int
    page: int = 1
    page_size: int = 20
