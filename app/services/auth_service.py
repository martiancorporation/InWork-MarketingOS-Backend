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
from app.core.rate_limit import clear as clear_rate_limit
from app.core.rate_limit import enforce as enforce_rate_limit
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    token_id_hash,
    verify_password,
)
from app.models.user import User, UserSession
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest

# A fixed, valid bcrypt hash to compare against when the email doesn't exist,
# so an unknown-email login costs the same bcrypt round-trip as a known-email
# wrong-password login. Without this, `or`-short-circuiting past
# `verify_password` for a nonexistent user makes that response measurably
# faster — a timing side-channel that defeats the identical-error-message
# anti-enumeration design below.
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-password-for-timing")

# A per-account backstop alongside the router's per-IP `RateLimit("login", ...)`
# — that one is keyed by IP, so a distributed or low-and-slow attack spread
# across many IPs against one specific account isn't throttled by it at all.
#
# Only *failed* attempts count (the budget is refunded on success), and the
# threshold sits well above human retry behaviour. Both matter: charging
# successful logins would let anyone who knows an email — the seeded admin
# address is in the README — hold a real account at 429 indefinitely by
# spending its budget, turning a brute-force defence into a denial-of-service
# lever against the very account it protects.
_ACCOUNT_LOCKOUT_SCOPE = "login-account"
_ACCOUNT_LOCKOUT_TIMES = 20
_ACCOUNT_LOCKOUT_SECONDS = 300


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.sessions = SessionRepository(db)

    def login(
        self, data: LoginRequest, *, user_agent: str | None = None, ip: str | None = None
    ) -> tuple[User, str]:
        # Account-level backstop: keyed by the submitted email itself (not
        # "does this account exist"), so it can't be used to probe which
        # emails have accounts — it throttles identically either way.
        account_key = data.email.lower()
        enforce_rate_limit(
            _ACCOUNT_LOCKOUT_SCOPE,
            account_key,
            times=_ACCOUNT_LOCKOUT_TIMES,
            seconds=_ACCOUNT_LOCKOUT_SECONDS,
        )
        user = self.users.get_by_email(data.email)
        # Always run the (deliberately slow) bcrypt compare, even when the
        # email is unknown — comparing against a fixed dummy hash keeps the
        # response time indistinguishable from a wrong-password attempt, so
        # the identical generic error message below can't be defeated by
        # timing the response.
        password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
        password_ok = verify_password(data.password, password_hash)
        if user is None or not password_ok:
            raise AuthError("Invalid email or password.")
        if not user.is_active:
            raise AuthError("This account is disabled.")
        # Authentic credentials — refund this account's budget so a legitimate
        # user's own successful logins can never contribute to locking them
        # out (see the constants above).
        clear_rate_limit(_ACCOUNT_LOCKOUT_SCOPE, account_key)

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
