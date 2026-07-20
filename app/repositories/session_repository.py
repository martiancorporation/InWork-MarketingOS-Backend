"""Data access for server-side auth sessions (token revocation).

A row exists for every live access token minted at login (keyed by a hash of the
token's ``jti``). Deleting the row revokes the token. Queries only — the auth
service owns the commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.user import UserSession
from app.repositories.base import BaseRepository


class SessionRepository(BaseRepository[UserSession]):
    model = UserSession

    def get_by_token_hash(self, token_hash: str) -> UserSession | None:
        return self.db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))

    def delete_by_token_hash(self, token_hash: str) -> bool:
        """Revoke a session by its token hash. Returns True if one was removed."""
        session = self.get_by_token_hash(token_hash)
        if session is None:
            return False
        self.db.delete(session)
        return True

    def delete_for_user(self, user_id: uuid.UUID) -> None:
        """Revoke every session for a user (log out everywhere)."""
        for session in self.db.scalars(
            select(UserSession).where(UserSession.user_id == user_id)
        ).all():
            self.db.delete(session)
