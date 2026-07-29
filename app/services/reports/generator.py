"""Turn a report request into a real uploaded file.

Ties ``content.py`` (data assembly) to the four ``render_*`` modules (bytes) and
``UploadService`` (S3 + a permanent permalink — see ``app/utils/download_link.py``).
Blocking work (DB queries, the sync renderers, the S3 upload) is offloaded to a
thread via ``anyio.to_thread.run_sync``, same pattern as the upload router and
brand extraction; the visual format's Playwright render is already async and is
simply awaited.
"""

from __future__ import annotations

from anyio import to_thread
from sqlalchemy.orm import Session

from app.integrations.storage import Storage
from app.models.client import Client
from app.models.enums import ReportFormat
from app.models.user import User
from app.schemas.report import ReportCreate
from app.schemas.upload import UploadRead
from app.services.reports.content import build_report_content
from app.services.reports.render_csv import render_csv
from app.services.reports.render_excel import render_excel
from app.services.reports.render_pdf import render_pdf
from app.services.reports.render_visual import render_visual
from app.services.upload_service import UploadService, sanitize_filename

_CONTENT_TYPES: dict[ReportFormat, str] = {
    ReportFormat.csv: "text/csv",
    ReportFormat.excel: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ReportFormat.pdf: "application/pdf",
    ReportFormat.visual: "image/jpeg",
}
_EXTENSIONS: dict[ReportFormat, str] = {
    ReportFormat.csv: ".csv",
    ReportFormat.excel: ".xlsx",
    ReportFormat.pdf: ".pdf",
    ReportFormat.visual: ".jpg",
}


async def generate_report_file(
    db: Session, storage: Storage, user: User, client: Client, data: ReportCreate
) -> UploadRead:
    content = await to_thread.run_sync(
        lambda: build_report_content(
            db,
            client,
            date_from=data.date_from,
            date_to=data.date_to,
            channels=data.channels,
            sections=data.sections,
        )
    )

    if data.format == ReportFormat.visual:
        file_bytes = await render_visual(content)
    elif data.format == ReportFormat.excel:
        file_bytes = await to_thread.run_sync(render_excel, content)
    elif data.format == ReportFormat.pdf:
        file_bytes = await to_thread.run_sync(render_pdf, content)
    else:
        file_bytes = await to_thread.run_sync(render_csv, content)

    filename = sanitize_filename(f"{data.title}{_EXTENSIONS[data.format]}")
    return await to_thread.run_sync(
        lambda: UploadService(db, storage).store_bytes(
            user,
            filename=filename,
            content_type=_CONTENT_TYPES[data.format],
            data=file_bytes,
            feature="reports.export",
        )
    )
