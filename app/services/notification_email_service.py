"""Notification email digest — a decoupled background sweep (not inline in
``NotificationService.notify()``) that sends each opted-in user one email
covering their new warning/critical notifications since the last sweep.

Mirrors ``ReportEmailService``'s shape exactly: an injectable ``BrevoClient``
(so tests pass a fake, never hitting the network), best-effort per-recipient
isolation (one user's failure never blocks another's, and is simply retried on
the next sweep since ``emailed_at`` stays null), and it never raises out of
``send_pending_emails`` — the scheduler job calls this unguarded.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.brevo.client import BrevoClient
from app.models.notification import Notification
from app.models.user import User
from app.repositories.notification_preference_repository import (
    NotificationPreferenceRepository,
)
from app.repositories.notification_repository import NotificationRepository

logger = logging.getLogger("app.notification_email")

_BATCH_LIMIT = 200


class NotificationEmailService:
    def __init__(self, db: Session, *, brevo_client: BrevoClient | None = None) -> None:
        self.db = db
        self.notifications = NotificationRepository(db)
        self.preferences = NotificationPreferenceRepository(db)
        self._brevo = brevo_client or BrevoClient()

    async def send_pending_emails(self, limit: int = _BATCH_LIMIT) -> int:
        """Send one digest email per eligible user. Returns how many users were emailed."""
        if not self._brevo.is_configured:
            return 0

        pending = self.notifications.list_pending_email(limit)
        if not pending:
            return 0

        by_user: dict[uuid.UUID, list[Notification]] = defaultdict(list)
        for n in pending:
            by_user[n.user_id].append(n)

        prefs = {p.user_id: p for p in self.preferences.list_for_users(list(by_user))}
        users = {
            u.id: u
            for u in self.db.scalars(
                select(User).where(User.id.in_(by_user), User.is_active.is_(True))
            )
        }

        sent = 0
        for user_id, items in by_user.items():
            pref = prefs.get(user_id)
            user = users.get(user_id)
            if pref is None or not pref.email_enabled or user is None:
                continue  # opted out (or never opted in) — never emailed, by default

            muted = set(pref.muted_client_ids)
            eligible = [n for n in items if n.client_id is None or str(n.client_id) not in muted]
            if not eligible:
                continue

            try:
                await self._send_digest(user, eligible)
            except Exception:
                logger.warning(
                    "Notification digest email failed for user %s", user_id, exc_info=True
                )
                continue  # leave emailed_at unset — retried on the next sweep

            now = datetime.now(UTC)
            for n in eligible:
                n.emailed_at = now
            self.db.commit()
            sent += 1
        return sent

    async def _send_digest(self, user: User, items: list[Notification]) -> None:
        rows = "".join(
            f"<li><strong>{escape(n.title)}</strong>" + (f" — {escape(n.body)}" if n.body else "") + "</li>"
            for n in items
        )
        html = f"<p>You have {len(items)} new notification(s):</p><ul>{rows}</ul>"
        await self._brevo.send_transactional_email(
            to=[{"email": user.email, "name": user.name}],
            subject=f"InWork MarketingOS — {len(items)} new notification(s)",
            html_content=html,
        )
