"""Unit tests for AuditMiddleware helpers: method gating, the submitted-payload
fallback diff (with secret redaction), request-body buffering + replay, and
revocation-aware actor resolution."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.middleware import (
    _AUDIT_METHODS,
    AuditMiddleware,
    _buffer_request_body,
    _changes_from_body,
)
from app.core.security import create_access_token, token_id_hash
from app.models.enums import UserRole
from app.models.user import User, UserSession


def test_only_mutating_methods_are_audited():
    assert _AUDIT_METHODS == {"POST", "PUT", "PATCH", "DELETE"}
    for read in ("GET", "HEAD", "OPTIONS"):
        assert read not in _AUDIT_METHODS


def test_changes_from_body_builds_before_after_diff():
    changes = _changes_from_body(b'{"name": "Acme", "status": "active"}')
    assert changes == {
        "name": {"before": None, "after": "Acme"},
        "status": {"before": None, "after": "active"},
    }


def test_changes_from_body_redacts_secrets():
    changes = _changes_from_body(b'{"email": "a@b.com", "password": "hunter2", "token": "abc"}')
    assert changes["email"]["after"] == "a@b.com"
    assert changes["password"]["after"] == "***redacted***"
    assert changes["token"]["after"] == "***redacted***"


def test_changes_from_body_ignores_non_objects_and_junk():
    assert _changes_from_body(b"") is None
    assert _changes_from_body(b"not json") is None
    assert _changes_from_body(b"[1, 2, 3]") is None  # not an object
    assert _changes_from_body(b"{}") is None  # empty
    assert _changes_from_body(b'{"x": 1}' + b" " * 70_000) is None  # oversized


def test_buffer_request_body_reads_and_replays():
    incoming = [
        {"type": "http.request", "body": b'{"a":', "more_body": True},
        {"type": "http.request", "body": b"1}", "more_body": False},
    ]

    async def run():
        pending = list(incoming)

        async def receive():
            return pending.pop(0)

        body, replay = await _buffer_request_body(receive, 64_000)
        # The middleware saw the whole body...
        assert body == b'{"a":1}'
        # ...and the app can replay every original message unchanged.
        replayed = [await replay(), await replay()]
        return body, replayed

    body, replayed = asyncio.run(run())
    assert replayed == incoming


def _bearer_request(token: str) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    return Request(scope)


def _user(db: Session) -> User:
    user = User(
        email="audit-actor@test.com",
        name="Audit Actor",
        password_hash="irrelevant",
        role=UserRole.user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_actor_resolves_a_live_session(db_session: Session) -> None:
    user = _user(db_session)
    jti = uuid.uuid4().hex
    db_session.add(
        UserSession(
            user_id=user.id,
            token_hash=token_id_hash(jti),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()
    token = create_access_token(user.id, jti=jti)

    actor = AuditMiddleware._actor(_bearer_request(token), db_session)

    assert actor == user.id


def test_actor_is_none_for_a_revoked_session(db_session: Session) -> None:
    """A token whose session row is gone (logged out) must not be attributed —
    otherwise a revoked token, which can never actually mutate anything, would
    still show up blamed on its original owner in the audit trail, disagreeing
    with get_current_user's own definition of "no longer authenticated"."""
    user = _user(db_session)
    jti = uuid.uuid4().hex
    # No matching UserSession row — as if logout already deleted it.
    token = create_access_token(user.id, jti=jti)

    actor = AuditMiddleware._actor(_bearer_request(token), db_session)

    assert actor is None


def test_actor_resolves_a_stateless_token_without_jti(db_session: Session) -> None:
    """A token with no jti stays stateless (backward compatible) — same rule
    as get_current_user."""
    user = _user(db_session)
    token = create_access_token(user.id)  # no jti

    actor = AuditMiddleware._actor(_bearer_request(token), db_session)

    assert actor == user.id
