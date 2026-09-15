"""Data access for users."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email.lower()))

    def get_many(self, ids: list[uuid.UUID]) -> list[User]:
        """Fetch users by id (order unspecified). Empty input → empty list."""
        if not ids:
            return []
        return list(self.db.scalars(select(User).where(User.id.in_(ids))).all())

    def email_exists(self, email: str) -> bool:
        return self.db.scalar(select(User.id).where(User.email == email.lower())) is not None

    def list(self, *, offset: int, limit: int) -> tuple[list[User], int]:
        total = int(self.db.scalar(select(func.count()).select_from(User)) or 0)
        rows = list(
            self.db.scalars(
                select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
            ).all()
        )
        return rows, total
