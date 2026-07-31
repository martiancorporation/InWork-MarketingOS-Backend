"""Expired ``user_sessions`` rows must be swept — nothing else ever deletes them
(logout only removes the one session behind the token being logged out)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.user import User, UserSession
from app.services.scheduler_service import SchedulerService


def _user(db: Session) -> User:
    user = User(
        email="purge-target@test.com",
        name="Purge Target",
        password_hash=hash_password("irrelevant1"),
        role=UserRole.user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _session(db: Session, user: User, *, expires_at: datetime, token_hash: str) -> UserSession:
    row = UserSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_purge_deletes_only_expired_sessions(db_session: Session) -> None:
    user = _user(db_session)
    now = datetime.now(UTC)
    expired = _session(db_session, user, expires_at=now - timedelta(hours=1), token_hash="expired")
    live = _session(db_session, user, expires_at=now + timedelta(hours=1), token_hash="live")
    # Capture plain ids before the purge commits — synchronize_session=False
    # leaves the deleted row's ORM instance in the identity map, and the commit
    # expires its attributes, so re-reading `expired.id` afterwards would try
    # (and fail) to refresh a row that's already gone.
    expired_id, live_id = expired.id, live.id

    deleted = SchedulerService(db_session).purge_expired_sessions()

    assert deleted == 1
    assert db_session.get(UserSession, expired_id) is None
    assert db_session.get(UserSession, live_id) is not None


def test_purge_is_a_noop_when_nothing_is_expired(db_session: Session) -> None:
    user = _user(db_session)
    _session(
        db_session,
        user,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        token_hash="still-live",
    )

    assert SchedulerService(db_session).purge_expired_sessions() == 0
