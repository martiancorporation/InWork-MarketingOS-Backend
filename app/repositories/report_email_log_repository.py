"""Data access for daily-report-email delivery records."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.models.report_email_log import ReportEmailLog
from app.repositories.base import BaseRepository


class ReportEmailLogRepository(BaseRepository[ReportEmailLog]):
    model = ReportEmailLog

    def get_for_date(self, client_id: uuid.UUID, report_date: date) -> ReportEmailLog | None:
        return self.db.scalar(
            select(ReportEmailLog).where(
                ReportEmailLog.client_id == client_id,
                ReportEmailLog.report_date == report_date,
            )
        )
