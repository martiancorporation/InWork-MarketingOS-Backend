"""Authentication use-cases: login + logout.

There is no sign-up. The first admin is provisioned by the seed script
(`scripts/seed_data.py`); further users are created by an admin via the
user-management API.

Login mints a JWT carrying a unique ``jti`` and records a matching
``UserSession`` so the token can be revoked server-side (BE-16); logout deletes
that session. ``get_current_user`` rejects any ``jti``-bearing token whose
session is gone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AuthError
from app.core.security import (
    create_access_token,
    decode_token,
    token_id_hash,
    verify_password,
)
from app.models.user import User, UserSession
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.sessions = SessionRepository(db)

    def login(
        self, data: LoginRequest, *, user_agent: str | None = None, ip: str | None = None
    ) -> tuple[User, str]:
        user = self.users.get_by_email(data.email)
        # Same generic error whether the email is unknown or the password is
        # wrong, so the endpoint never reveals which emails have accounts.
        if user is None or not verify_password(data.password, user.password_hash):
            raise AuthError("Invalid email or password.")
        if not user.is_active:
            raise AuthError("This account is disabled.")

        # Mint a revocable token: unique jti + matching session row.
        jti = uuid.uuid4().hex
        expire_minutes = get_settings().security.access_token_expire_minutes
        expires_at = datetime.now(UTC) + timedelta(minutes=expire_minutes)
        self.sessions.add(
            UserSession(
                user_id=user.id,
                token_hash=token_id_hash(jti),
                user_agent=user_agent,
                ip_address=ip,
                expires_at=expires_at,
            )
        )

        user.last_login_at = datetime.now(UTC)
        token = create_access_token(user.id, jti=jti)
        self.db.commit()
        self.db.refresh(user)
        return user, token

    def logout(self, token: str) -> None:
        """Revoke the session behind ``token`` (idempotent).

        Decodes without verifying expiry — an expired token is already dead but
        we still clean up its row. A token without a ``jti`` (stateless) is a
        no-op.
        """
        try:
            payload = decode_token(token, verify_exp=False)
        except jwt.PyJWTError:
            return
        jti = payload.get("jti")
        if jti is None:
            return
        self.sessions.delete_by_token_hash(token_id_hash(str(jti)))
        self.db.commit()
