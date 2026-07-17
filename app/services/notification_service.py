"""Notification use-cases — create/deliver in-app notifications and read the
current user's notification centre.

A notification targets one recipient user. ``notify_client_team`` fans a signal
out to every user assigned to a client (the people "tagged to that project").
``rec_key`` deduplicates: a repeat signal updates the existing unread
notification instead of stacking. Repositories flush; this service commits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.models.enums import NotificationLevel
from app.models.notification import Notification
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.notification_repository import NotificationRepository
from app.schemas.notification import NotificationListResponse, NotificationRead


class NotificationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.notifications = NotificationRepository(db)

    # ---- create / deliver --------------------------------------------- #

    def notify(
        self,
        user_id: uuid.UUID,
        *,
        title: str,
        kind: str = "info",
        level: NotificationLevel | str = NotificationLevel.info,
        body: str | None = None,
        client_id: uuid.UUID | None = None,
        link: str | None = None,
        rec_key: str | None = None,
        commit: bool = True,
    ) -> Notification:
        """Create (or, when a ``rec_key`` matches an unread one, refresh) a notification."""
        level_value = getattr(level, "value", level)
        existing = (
            self.notifications.find_unread_by_reckey(user_id, rec_key) if rec_key else None
        )
        if existing is not None:
            existing.title = title
            existing.body = body
            existing.level = level_value
            existing.kind = kind
            existing.link = link
            existing.client_id = client_id
            notification = existing
        else:
            notification = Notification(
                user_id=user_id, kind=kind, level=level_value, title=title,
                body=body, client_id=client_id, link=link, rec_key=rec_key,
            )
            self.notifications.add(notification)
        if commit:
            self.db.commit()
            self.db.refresh(notification)
        return notification

    def notify_client_team(
        self, client_id: uuid.UUID, *, title: str, **kwargs
    ) -> int:
        """Notify every user assigned to a client. Returns how many were notified."""
        assignments = AssignmentRepository(self.db).list_for_client(client_id)
        for a in assignments:
            self.notify(
                a.user_id, title=title, client_id=client_id, commit=False, **kwargs
            )
        self.db.commit()
        return len(assignments)

    # ---- read side ---------------------------------------------------- #

    def list_for_user(
        self, user_id: uuid.UUID, *, pagination: PaginationParams, unread_only: bool = False
    ) -> NotificationListResponse:
        rows, total = self.notifications.list_for_user(
            user_id, unread_only=unread_only, offset=pagination.offset, limit=pagination.limit
        )
        return NotificationListResponse(
            items=[NotificationRead.model_validate(n) for n in rows],
            total=total,
            unread=self.notifications.unread_count(user_id),
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def unread_count(self, user_id: uuid.UUID) -> int:
        return self.notifications.unread_count(user_id)

    def mark_read(self, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
        notification = self.notifications.get_for_user(user_id, notification_id)
        if notification is None:
            raise NotFoundError("Notification not found.")
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            self.db.commit()
            self.db.refresh(notification)
        return notification

    def mark_all_read(self, user_id: uuid.UUID) -> int:
        rows, _ = self.notifications.list_for_user(user_id, unread_only=True, limit=None)
        now = datetime.now(UTC)
        for n in rows:
            n.read_at = now
        self.db.commit()
        return len(rows)
