"""Generated client reports (performance / compliance / strategy / executive)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import ReportFormat, ReportKind

if TYPE_CHECKING:
    from app.models.client import Client


class Report(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_client_created", "client_id", "created_at"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ReportKind] = mapped_column(
        pg_enum(ReportKind, "report_kind"), nullable=False, default=ReportKind.performance
    )
    format: Mapped[ReportFormat] = mapped_column(
        pg_enum(ReportFormat, "report_format"), nullable=False, default=ReportFormat.pdf
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    sections: Mapped[dict | None] = mapped_column(JSONB)  # selected sections/channels
    file_url: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped["Client"] = relationship(back_populates="reports")
