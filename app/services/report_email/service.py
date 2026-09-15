"""Daily report email orchestration — the entry point the scheduler calls once
per client per due date (``app/services/report_email/timing.py`` decides
"due").

Owns: recipient resolution (assigned users ∪ admins — never the client's own
contacts), idempotency against ``report_email_log``, retrying the Brevo send,
and writing the delivery/audit trail. Never raises out of
``send_daily_report`` — the caller (``SchedulerService``'s sweep) isolates
per-client failures already, but this method also protects itself so one
client's bug can't take the sweep down from the inside.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.daily_report import DailyReportAgent
from app.integrations.brevo.client import BrevoClient, BrevoSendError
from app.integrations.llm import LLMClient
from app.models.client import Client
from app.models.enums import ReportEmailStatus, UserRole
from app.models.report_email_log import ReportEmailLog
from app.models.user import User
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.report_email_log_repository import ReportEmailLogRepository
from app.services.audit_service import AuditService
from app.services.report_email.data import build_daily_report_data
from app.services.report_email.render import render_daily_report_html

logger = logging.getLogger("app.report_email")

_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (2, 6)


class ReportEmailService:
    def __init__(
        self,
        db: Session,
        *,
        ai_client: LLMClient | None = None,
        brevo_client: BrevoClient | None = None,
    ) -> None:
        self.db = db
        self.logs = ReportEmailLogRepository(db)
        self._brevo = brevo_client or BrevoClient()
        self._agent = DailyReportAgent(ai_client)

    def _resolve_recipients(self, client_id: uuid.UUID) -> list[tuple[str, str]]:
        """Assigned users ∪ active admins, deduped. Never touches
        ``ClientContact`` — the structural guarantee the client is never
        emailed."""
        assigned_ids = {a.user_id for a in AssignmentRepository(self.db).list_for_client(client_id)}
        users: dict[uuid.UUID, User] = {}
        if assigned_ids:
            for u in self.db.scalars(
                select(User).where(User.id.in_(assigned_ids), User.is_active.is_(True))
            ):
                users[u.id] = u
        for u in self.db.scalars(
            select(User).where(User.role == UserRole.admin, User.is_active.is_(True))
        ):
            users[u.id] = u
        return [(u.name, u.email) for u in users.values()]

    async def send_daily_report(self, client: Client, report_date: date) -> ReportEmailLog:
        existing = self.logs.get_for_date(client.id, report_date)
        if existing is not None and existing.status == ReportEmailStatus.sent.value:
            return existing  # already delivered — no-op

        recipients = self._resolve_recipients(client.id)
        if not recipients:
            return self._write_log(
                existing,
                client.id,
                report_date,
                status=ReportEmailStatus.skipped_no_recipients,
                recipients=[],
            )

        if not self._brevo.is_configured:
            logger.warning("Brevo not configured — skipping daily report for client %s", client.id)
            return self._write_log(
                existing,
                client.id,
                report_date,
                status=ReportEmailStatus.skipped_not_configured,
                recipients=[],
            )

        try:
            data = build_daily_report_data(self.db, client, report_date)
            narrative = await self._agent.generate(data)
            html = render_daily_report_html(data, narrative)
        except Exception as exc:
            logger.exception("Failed to build daily report for client %s", client.id)
            return self._write_log(
                existing,
                client.id,
                report_date,
                status=ReportEmailStatus.failed,
                recipients=recipients,
                error=str(exc)[:500],
            )

        subject = f"{client.name} — Daily Report — {report_date.isoformat()}"
        to = [{"email": email, "name": name} for name, email in recipients]

        attempt = 0
        error: str | None = None
        message_id: str | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                message_id = await self._brevo.send_transactional_email(
                    to=to, subject=subject, html_content=html
                )
                error = None
                break
            except BrevoSendError as exc:
                error = str(exc)[:500]
                if not exc.retryable or attempt == _MAX_ATTEMPTS:
                    break
                await asyncio.sleep(
                    _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
                )

        if error is not None:
            logger.warning(
                "Daily report send failed for client %s after %d attempt(s): %s",
                client.id,
                attempt,
                error,
            )
            return self._write_log(
                existing,
                client.id,
                report_date,
                status=ReportEmailStatus.failed,
                recipients=recipients,
                error=error,
                attempts=attempt,
            )

        log = self._write_log(
            existing,
            client.id,
            report_date,
            status=ReportEmailStatus.sent,
            recipients=recipients,
            brevo_message_id=message_id,
            attempts=attempt,
            sent_at=datetime.now(UTC),
        )
        AuditService(self.db).record(
            entity="clients",
            entity_id=client.id,
            client_id=client.id,
            action="report.daily_email.sent",
            target_label=client.name,
            meta={"report_date": report_date.isoformat(), "recipient_count": len(recipients)},
        )
        return log

    def _write_log(
        self,
        existing: ReportEmailLog | None,
        client_id: uuid.UUID,
        report_date: date,
        *,
        status: ReportEmailStatus,
        recipients: list[tuple[str, str]],
        error: str | None = None,
        attempts: int = 0,
        brevo_message_id: str | None = None,
        sent_at: datetime | None = None,
    ) -> ReportEmailLog:
        row = existing
        is_new = row is None
        if row is None:
            row = ReportEmailLog(client_id=client_id, report_date=report_date)
            self.logs.add(row)
        row.status = status.value
        row.recipient_count = len(recipients)
        row.recipient_emails = [email for _, email in recipients] or None
        row.error = error
        row.attempt_count = attempts
        row.brevo_message_id = brevo_message_id
        row.sent_at = sent_at
        try:
            self.db.commit()
        except IntegrityError:
            # Another process inserted this (client_id, report_date) first —
            # the unique constraint is the idempotency backstop. Defer to it.
            self.db.rollback()
            if is_new:
                refreshed = self.logs.get_for_date(client_id, report_date)
                if refreshed is not None:
                    return refreshed
            raise
        self.db.refresh(row)
        return row
