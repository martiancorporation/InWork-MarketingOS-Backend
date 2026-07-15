"""Data access for generated client reports (hard-filtered by client_id)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.enums import ReportKind
from app.models.report import Report
from app.repositories.base import BaseRepository


class ReportRepository(BaseRepository[Report]):
    model = Report

    def get_for_client(
        self, client_id: uuid.UUID, report_id: uuid.UUID
    ) -> Report | None:
        return self.db.scalar(
            select(Report).where(
                Report.id == report_id, Report.client_id == client_id
            )
        )

    def list_for_client(
        self, client_id: uuid.UUID, *, kind: ReportKind | None = None
    ) -> list[Report]:
        stmt = select(Report).where(Report.client_id == client_id)
        if kind is not None:
            stmt = stmt.where(Report.kind == kind)
        stmt = stmt.order_by(Report.created_at.desc())
        return list(self.db.scalars(stmt).all())
