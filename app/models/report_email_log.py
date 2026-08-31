"""Delivery record for the daily report email — one row per client per
report date, sent to the internal team (never the client).

The unique constraint on ``(client_id, report_date)`` **is** the idempotency
mechanism: a duplicate send attempt (e.g. two overlapping scheduler ticks or
processes) hits ``IntegrityError`` on insert, which the service layer treats
as "already handled" rather than sending twice.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, JSONColumn, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin
from app.models.enums import ReportEmailStatus

if TYPE_CHECKING:
    from app.models.client import Client


class ReportEmailLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_email_log"
    __table_args__ = (UniqueConstraint("client_id", "report_date"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The client-local calendar day the report covers (not the UTC send time).
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ReportEmailStatus.failed.value
    )
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recipient_emails: Mapped[list[str] | None] = mapped_column(JSONColumn)
    brevo_message_id: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    client: Mapped[Client] = relationship(back_populates="report_email_logs")
