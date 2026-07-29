"""Report registry use-cases: record, list, fetch, update, and delete reports.

Creating a report actually generates and uploads a real file (CSV/Excel/PDF/JPEG
— see ``app/services/reports/generator.py``) *before* the registry row is
constructed, mirroring ``UploadService.store_bytes``'s "push before commit"
ordering: if generation fails, nothing is persisted and the caller gets a real
error, never a row with an empty ``file_url``. ``update_report`` stays a plain
manual-override endpoint (``file_url`` is settable directly, unvalidated) — an
intentional escape hatch for attaching/replacing a file out of band. Client-
access scoping is enforced at the router. Repositories flush; this service owns
the commit.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.core.pagination import PaginationParams
from app.integrations.storage import Storage
from app.models.client import Client
from app.models.enums import ReportKind
from app.models.report import Report
from app.models.user import User
from app.repositories.report_repository import ReportRepository
from app.schemas.report import ReportCreate, ReportListResponse, ReportRead, ReportUpdate
from app.services.reports.generator import generate_report_file

logger = logging.getLogger("app.services.reports")


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.reports = ReportRepository(db)

    def list_reports(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        kind: ReportKind | None = None,
    ) -> ReportListResponse:
        rows, total = self.reports.list_for_client(
            client_id, kind=kind, offset=pagination.offset, limit=pagination.limit
        )
        items = [ReportRead.model_validate(r) for r in rows]
        return ReportListResponse(
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_report(self, client_id: uuid.UUID, report_id: uuid.UUID) -> Report:
        report = self.reports.get_for_client(client_id, report_id)
        if report is None:
            raise NotFoundError("Report not found.")
        return report

    async def create_report(
        self,
        client: Client,
        data: ReportCreate,
        *,
        user: User,
        storage: Storage,
    ) -> Report:
        try:
            upload = await generate_report_file(self.db, storage, user, client, data)
        except Exception:  # generation genuinely failed — say so, don't fake success
            logger.warning(
                "Report generation failed for client %s (%s/%s)",
                client.id,
                data.kind,
                data.format,
                exc_info=True,
            )
            raise ServiceUnavailableError(
                "Could not generate the report file. Please try again."
            ) from None

        report = Report(
            client_id=client.id,
            kind=data.kind,
            format=data.format,
            title=data.title,
            date_from=data.date_from,
            date_to=data.date_to,
            scope=data.scope,
            channels=data.channels,
            sections=data.sections,
            save_to_outlook_draft=data.save_to_outlook_draft,
            file_url=upload.download_url,
            created_by=user.id,
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
