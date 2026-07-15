"""Data access for generated client reports (hard-filtered by client_id)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

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
        self,
        client_id: uuid.UUID,
        *,
        kind: ReportKind | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Report], int]:
        """Return a page of reports plus the total matching count (DB-side)."""
        conditions = [Report.client_id == client_id]
        if kind is not None:
            conditions.append(Report.kind == kind)

        total = self.db.scalar(
            select(func.count()).select_from(Report).where(*conditions)
        )
        stmt = (
            select(Report)
            .where(*conditions)
            .order_by(Report.created_at.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)
