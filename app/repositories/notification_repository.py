"""Data access for per-user notifications. Every query is scoped to ``user_id``."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    model = Notification

    def get_for_user(
        self, user_id: uuid.UUID, notification_id: uuid.UUID
    ) -> Notification | None:
        return self.db.scalar(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user_id
            )
        )

    def find_unread_by_reckey(
        self, user_id: uuid.UUID, rec_key: str
    ) -> Notification | None:
        return self.db.scalar(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.rec_key == rec_key,
                Notification.read_at.is_(None),
            )
        )

    def unread_count(self, user_id: uuid.UUID) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            )
            or 0
        )

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Notification], int]:
        conditions = [Notification.user_id == user_id]
        if unread_only:
            conditions.append(Notification.read_at.is_(None))
        total = self.db.scalar(
            select(func.count()).select_from(Notification).where(*conditions)
        )
        stmt = (
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)
