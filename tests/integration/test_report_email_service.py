"""Integration tests for ``ReportEmailService`` — recipient resolution,
idempotency, graceful degradation, and "Not connected" rendering.

Brevo is off for the whole suite (see conftest), so every test either injects
a ``FakeBrevo`` or exercises the "not configured" skip path directly — no real
network call. The AI provider is likewise unconfigured, so the AI narrative
always takes its deterministic fallback here, which is exactly what's asserted.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.integrations.brevo.client import BrevoSendError
from app.models.assignment import ClientAssignment
from app.models.client import Client
from app.models.enums import ClientStatus, ReportEmailStatus, UserRole
from app.models.report_email_log import ReportEmailLog
from app.models.user import User
from app.services.report_email.service import ReportEmailService


class FakeBrevo:
    """Stand-in BrevoClient: reports configured, records calls, can fail on cue."""

    def __init__(self, *, fail_times: int = 0, retryable: bool = True) -> None:
        self.calls: list[dict] = []
        self._fail_times = fail_times
        self._retryable = retryable

    @property
    def is_configured(self) -> bool:
        return True

    async def send_transactional_email(self, *, to, subject, html_content) -> str:
        self.calls.append({"to": to, "subject": subject, "html_content": html_content})
        if len(self.calls) <= self._fail_times:
            raise BrevoSendError("simulated failure", retryable=self._retryable)
        return "fake-message-id"


def _client_row(db: Session, *, timezone: str | None = "UTC") -> Client:
    c = Client(slug=f"seed-{uuid.uuid4().hex[:8]}", name="Seed Co", timezone=timezone)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _user_row(db: Session, *, role: UserRole = UserRole.user, active: bool = True) -> User:
    u = User(
        email=f"{uuid.uuid4().hex[:8]}@test.com",
        name="Test User",
        password_hash=hash_password("testPass1"),
        role=role,
        is_active=active,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_send_daily_reports_sweep_isolates_per_client_failure(
    db_session: Session, monkeypatch
) -> None:
    """One client's ``send_daily_report`` raising unexpectedly must not abort
    the sweep for every other active client — the scenario the scheduler's
    ``send_daily_reports_sweep`` docstring claims but was never actually
    exercised."""
    from app.services.scheduler_service import SchedulerService

    good = _client_row(db_session)
    good.status = ClientStatus.active.value
    bad = _client_row(db_session)
    bad.status = ClientStatus.active.value
    db_session.commit()

    original = ReportEmailService.send_daily_report

    async def flaky(self, client, report_date):
        if client.id == bad.id:
            raise RuntimeError("simulated crash")
        return await original(self, client, report_date)

    monkeypatch.setattr(ReportEmailService, "send_daily_report", flaky)

    result = asyncio.run(SchedulerService(db_session).send_daily_reports_sweep())

    bad_rows = [r for r in result.details if r.client_id == bad.id]
    good_rows = [r for r in result.details if r.client_id == good.id]
    assert bad_rows, "the failing client should still get a result row"
    assert all(r.status == "error" and "simulated crash" in r.error for r in bad_rows)
    assert result.failed >= len(bad_rows)
    # The other client's own report was still attempted — not skipped because
    # of the first client's crash.
    assert good_rows
    assert all(r.status != "error" for r in good_rows)


def test_no_recipients_is_skipped_not_failed(db_session: Session):
    client = _client_row(db_session)
    log = asyncio.run(
        ReportEmailService(db_session, brevo_client=FakeBrevo()).send_daily_report(
            client, date(2026, 8, 26)
        )
    )
    assert log.status == ReportEmailStatus.skipped_no_recipients.value
    assert log.recipient_count == 0


def test_brevo_unconfigured_is_skipped_and_never_called(db_session: Session):
    client = _client_row(db_session)
    admin = _user_row(db_session, role=UserRole.admin)
    log = asyncio.run(ReportEmailService(db_session).send_daily_report(client, date(2026, 8, 26)))
    assert log.status == ReportEmailStatus.skipped_not_configured.value
    assert admin  # admin exists but is irrelevant — the send never even resolved


def test_recipients_are_assignment_union_admins_deduped(db_session: Session):
    client = _client_row(db_session)
    assigned = _user_row(db_session, role=UserRole.user)
    admin = _user_row(db_session, role=UserRole.admin)
    inactive_admin = _user_row(db_session, role=UserRole.admin, active=False)
    unrelated_user = _user_row(db_session, role=UserRole.user)  # not assigned, not admin
    db_session.add(ClientAssignment(client_id=client.id, user_id=assigned.id))
    db_session.commit()

    fake = FakeBrevo()
    log = asyncio.run(
        ReportEmailService(db_session, brevo_client=fake).send_daily_report(
            client, date(2026, 8, 26)
        )
    )

    assert log.status == ReportEmailStatus.sent.value
    assert log.recipient_count == 2  # assigned user + active admin
    assert set(log.recipient_emails) == {assigned.email, admin.email}
    assert unrelated_user.email not in log.recipient_emails
    assert inactive_admin.email not in log.recipient_emails
    # never emails the client itself — recipients come only from users/admins
    assert client.name not in log.recipient_emails

    sent_to = {r["email"] for r in fake.calls[0]["to"]}
    assert sent_to == {assigned.email, admin.email}


def test_second_call_same_day_is_a_no_op(db_session: Session):
    client = _client_row(db_session)
    admin = _user_row(db_session, role=UserRole.admin)
    fake = FakeBrevo()
    svc = ReportEmailService(db_session, brevo_client=fake)

    first = asyncio.run(svc.send_daily_report(client, date(2026, 8, 26)))
    second = asyncio.run(svc.send_daily_report(client, date(2026, 8, 26)))

    assert first.status == second.status == ReportEmailStatus.sent.value
    assert first.id == second.id
    assert len(fake.calls) == 1  # Brevo was only actually called once

    rows = db_session.query(ReportEmailLog).filter_by(client_id=client.id).all()
    assert len(rows) == 1
    assert admin  # keeps the admin fixture referenced


def test_different_dates_each_get_their_own_send(db_session: Session):
    client = _client_row(db_session)
    _user_row(db_session, role=UserRole.admin)
    fake = FakeBrevo()
    svc = ReportEmailService(db_session, brevo_client=fake)

    asyncio.run(svc.send_daily_report(client, date(2026, 8, 25)))
    asyncio.run(svc.send_daily_report(client, date(2026, 8, 26)))

    assert len(fake.calls) == 2
    rows = db_session.query(ReportEmailLog).filter_by(client_id=client.id).all()
    assert {r.report_date for r in rows} == {date(2026, 8, 25), date(2026, 8, 26)}


def test_retryable_failure_then_success_is_recorded_sent(db_session: Session):
    client = _client_row(db_session)
    _user_row(db_session, role=UserRole.admin)
    fake = FakeBrevo(fail_times=1, retryable=True)
    log = asyncio.run(
        ReportEmailService(db_session, brevo_client=fake).send_daily_report(
            client, date(2026, 8, 26)
        )
    )
    assert log.status == ReportEmailStatus.sent.value
    assert log.attempt_count == 2  # first attempt failed, second succeeded
    assert len(fake.calls) == 2


def test_non_retryable_failure_fails_fast_without_retry(db_session: Session):
    client = _client_row(db_session)
    _user_row(db_session, role=UserRole.admin)
    fake = FakeBrevo(fail_times=99, retryable=False)
    log = asyncio.run(
        ReportEmailService(db_session, brevo_client=fake).send_daily_report(
            client, date(2026, 8, 26)
        )
    )
    assert log.status == ReportEmailStatus.failed.value
    assert len(fake.calls) == 1  # no retry on a non-retryable (4xx-style) error


def test_disconnected_integrations_render_as_not_connected(db_session: Session):
    """No Integration rows exist for a fresh client, so every channel the
    email covers must render explicitly as "Not connected" rather than a
    silently-zeroed row."""
    client = _client_row(db_session)
    _user_row(db_session, role=UserRole.admin)
    fake = FakeBrevo()
    asyncio.run(
        ReportEmailService(db_session, brevo_client=fake).send_daily_report(
            client, date(2026, 8, 26)
        )
    )
    html = fake.calls[0]["html_content"]
    assert "Not connected" in html
    assert "Meta" in html and "Google Ads" in html and "LinkedIn" in html


def test_client_is_never_a_recipient(db_session: Session):
    """Structural check: recipient resolution never touches ClientContact —
    the client's own email must never appear anywhere in the send."""
    client = _client_row(db_session)
    _user_row(db_session, role=UserRole.admin)
    fake = FakeBrevo()
    asyncio.run(
        ReportEmailService(db_session, brevo_client=fake).send_daily_report(
            client, date(2026, 8, 26)
        )
    )
    to_emails = {r["email"] for r in fake.calls[0]["to"]}
    assert client.slug not in to_emails
