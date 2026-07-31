"""Data access for server-side auth sessions (token revocation).

A row exists for every live access token minted at login (keyed by a hash of the
token's ``jti``). Deleting the row revokes the token. Queries only — the auth
service owns the commit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select

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

    def purge_expired(self, *, now: datetime) -> int:
        """Bulk-delete every session past its expiry. Caller commits.

        A row here only ever matters for the revocation check in
        ``get_current_user`` (which already rejects an expired token via the
        JWT's own ``exp`` claim) — an expired row is dead weight, not a security
        gap, but nothing ever swept the table, so it grows without bound.
        """
        # synchronize_session=False: a plain bulk DELETE, no ORM identity-map
        # sync — the default "evaluate" strategy compares expires_at in Python
        # against already-loaded objects and blows up on SQLite's naive
        # datetimes vs. our timezone-aware `now`.
        stmt = (
            delete(UserSession)
            .where(UserSession.expires_at < now)
            .execution_options(synchronize_session=False)
        )
        result = self.db.execute(stmt)
        return result.rowcount or 0
