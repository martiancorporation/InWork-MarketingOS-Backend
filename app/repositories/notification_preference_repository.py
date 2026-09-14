"""Data access for per-user notification preferences (one row per user)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.notification import NotificationPreference
from app.repositories.base import BaseRepository


class NotificationPreferenceRepository(BaseRepository[NotificationPreference]):
    model = NotificationPreference

    def get_for_user(self, user_id: uuid.UUID) -> NotificationPreference | None:
        return self.db.scalar(
            select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        )

    def list_for_users(self, user_ids: list[uuid.UUID]) -> list[NotificationPreference]:
        """Bulk lookup so the email sweep isn't N+1 across recipients."""
        if not user_ids:
            return []
        return list(
            self.db.scalars(
                select(NotificationPreference).where(
                    NotificationPreference.user_id.in_(user_ids)
                )
            ).all()
        )
