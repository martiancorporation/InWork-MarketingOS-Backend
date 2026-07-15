"""Report registry use-cases: record, list, fetch, update, and delete reports.

The report row captures the *definition* of a generated report (config + optional
file pointer); rendering the actual PDF/Excel bytes stays a separate concern (the
web exports client-side today, and a rendered file can be attached later via
``file_url``). Client-access scoping is enforced at the router. Repositories
flush; this service owns the commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.enums import ReportKind
from app.models.report import Report
from app.repositories.report_repository import ReportRepository
from app.schemas.report import ReportCreate, ReportListResponse, ReportRead, ReportUpdate


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.reports = ReportRepository(db)

    def list_reports(
        self, client_id: uuid.UUID, *, kind: ReportKind | None = None
    ) -> ReportListResponse:
        rows = self.reports.list_for_client(client_id, kind=kind)
        items = [ReportRead.model_validate(r) for r in rows]
        return ReportListResponse(items=items, total=len(items))

    def get_report(self, client_id: uuid.UUID, report_id: uuid.UUID) -> Report:
        report = self.reports.get_for_client(client_id, report_id)
        if report is None:
            raise NotFoundError("Report not found.")
        return report

    def create_report(
        self, client_id: uuid.UUID, data: ReportCreate, *, created_by: uuid.UUID
    ) -> Report:
        report = Report(
            client_id=client_id,
            kind=data.kind,
            format=data.format,
            title=data.title,
            date_from=data.date_from,
            date_to=data.date_to,
            scope=data.scope,
            channels=data.channels,
            sections=data.sections,
            save_to_outlook_draft=data.save_to_outlook_draft,
            file_url=data.file_url,
            created_by=created_by,
        )
        self.reports.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    def update_report(
        self, client_id: uuid.UUID, report_id: uuid.UUID, data: ReportUpdate
    ) -> Report:
        report = self.get_report(client_id, report_id)
        fields = data.model_fields_set
        if "title" in fields and data.title is not None:
            report.title = data.title
        if "file_url" in fields:
            report.file_url = data.file_url
        if "save_to_outlook_draft" in fields and data.save_to_outlook_draft is not None:
            report.save_to_outlook_draft = data.save_to_outlook_draft
        self.db.commit()
        self.db.refresh(report)
        return report

    def delete_report(self, client_id: uuid.UUID, report_id: uuid.UUID) -> None:
        report = self.get_report(client_id, report_id)
        self.db.delete(report)
        self.db.commit()
