"""Integration tests for ``NotificationEmailService`` — eligibility filtering
(level, opt-in, per-client mute) and idempotency via ``emailed_at``.

Brevo is off for the whole suite (see conftest), so every test either injects
a ``FakeBrevo`` (mirrors ``test_report_email_service.py``'s pattern) or
exercises the "not configured" skip path directly — no real network call.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.client import Client
from app.models.enums import NotificationLevel, UserRole
from app.models.notification import Notification, NotificationPreference
from app.models.user import User
from app.services.notification_email_service import NotificationEmailService


def _client_row(db: Session) -> Client:
    c = Client(slug=f"seed-{uuid.uuid4().hex[:8]}", name="Seed Co")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


class FakeBrevo:
    """Stand-in BrevoClient: reports configured, records calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def is_configured(self) -> bool:
        return True

    async def send_transactional_email(self, *, to, subject, html_content) -> str:
        self.calls.append({"to": to, "subject": subject, "html_content": html_content})
        return "fake-message-id"


def _user_row(db: Session, **overrides) -> User:
    u = User(
        email=f"{uuid.uuid4().hex[:8]}@test.com",
        name="Test User",
        password_hash=hash_password("testPass1"),
        role=UserRole.user,
        is_active=True,
    )
    for k, v in overrides.items():
        setattr(u, k, v)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _notification_row(db: Session, user_id, **overrides) -> Notification:
    n = Notification(
        user_id=user_id,
        title="Something happened",
        level=NotificationLevel.warning.value,
        kind="alert",
    )
    for k, v in overrides.items():
        setattr(n, k, v)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def _pref_row(db: Session, user_id, **overrides) -> NotificationPreference:
    p = NotificationPreference(user_id=user_id, email_enabled=True, muted_client_ids=[])
    for k, v in overrides.items():
        setattr(p, k, v)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_not_configured_sends_nothing(db_session: Session):
    user = _user_row(db_session)
    _pref_row(db_session, user.id)
    _notification_row(db_session, user.id)

    class NotConfigured:
        is_configured = False

    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=NotConfigured()).send_pending_emails()
    )
    assert sent == 0


def test_sends_digest_for_opted_in_user(db_session: Session):
    user = _user_row(db_session)
    _pref_row(db_session, user.id)
    n1 = _notification_row(db_session, user.id, title="Budget alert")
    n2 = _notification_row(db_session, user.id, title="Another alert")

    brevo = FakeBrevo()
    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=brevo).send_pending_emails()
    )
    assert sent == 1
    assert len(brevo.calls) == 1
    assert brevo.calls[0]["to"] == [{"email": user.email, "name": user.name}]
    assert "Budget alert" in brevo.calls[0]["html_content"]
    assert "Another alert" in brevo.calls[0]["html_content"]

    db_session.refresh(n1)
    db_session.refresh(n2)
    assert n1.emailed_at is not None
    assert n2.emailed_at is not None


def test_info_level_never_emailed(db_session: Session):
    user = _user_row(db_session)
    _pref_row(db_session, user.id)
    _notification_row(db_session, user.id, level=NotificationLevel.info.value)

    brevo = FakeBrevo()
    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=brevo).send_pending_emails()
    )
    assert sent == 0
    assert brevo.calls == []


def test_opted_out_user_not_emailed(db_session: Session):
    user = _user_row(db_session)
    _pref_row(db_session, user.id, email_enabled=False)
    _notification_row(db_session, user.id)

    brevo = FakeBrevo()
    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=brevo).send_pending_emails()
    )
    assert sent == 0


def test_no_preference_row_defaults_to_no_email(db_session: Session):
    user = _user_row(db_session)
    _notification_row(db_session, user.id)  # no NotificationPreference row at all

    brevo = FakeBrevo()
    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=brevo).send_pending_emails()
    )
    assert sent == 0


def test_muted_client_excluded(db_session: Session):
    user = _user_row(db_session)
    seeded_client = _client_row(db_session)
    _pref_row(db_session, user.id, muted_client_ids=[str(seeded_client.id)])
    _notification_row(db_session, user.id, client_id=seeded_client.id)

    brevo = FakeBrevo()
    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=brevo).send_pending_emails()
    )
    assert sent == 0


def test_already_emailed_not_resent(db_session: Session):
    from datetime import UTC, datetime

    user = _user_row(db_session)
    _pref_row(db_session, user.id)
    _notification_row(db_session, user.id, emailed_at=datetime.now(UTC))

    brevo = FakeBrevo()
    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=brevo).send_pending_emails()
    )
    assert sent == 0
    assert brevo.calls == []


def test_inactive_user_skipped(db_session: Session):
    user = _user_row(db_session, is_active=False)
    _pref_row(db_session, user.id)
    _notification_row(db_session, user.id)

    brevo = FakeBrevo()
    sent = asyncio.run(
        NotificationEmailService(db_session, brevo_client=brevo).send_pending_emails()
    )
    assert sent == 0
