"""Security primitives: password hashing and JWT access tokens.

Kept free of framework/HTTP concerns so it can be unit-tested in isolation and
reused anywhere. Secrets/algorithm come from settings — never hardcoded.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings

TOKEN_TYPE_ACCESS = "access"


def _prehash(plain_password: str) -> bytes:
    """SHA-256 → base64 so any-length password fits bcrypt's 72-byte input limit.

    (The standard "sha256+bcrypt" construction — avoids bcrypt silently
    truncating long passwords or raising on them.)
    """
    digest = hashlib.sha256(plain_password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(_prehash(plain_password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(plain_password), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    subject: str | uuid.UUID,
    *,
    expires_minutes: int | None = None,
    jti: str | None = None,
) -> str:
    """Create a signed, short-lived JWT access token for ``subject`` (user id).

    Pass ``jti`` (a unique token id) to make the token server-side revocable: the
    caller stores a matching session row and ``get_current_user`` rejects the
    token once that row is gone. Omit it for a plain stateless token.
    """
    sec = get_settings().security
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=expires_minutes or sec.access_token_expire_minutes)
    payload: dict = {
        "sub": str(subject),
        "type": TOKEN_TYPE_ACCESS,
        "iat": now,
        "exp": expire,
    }
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, sec.secret_key, algorithm=sec.algorithm)


def token_id_hash(jti: str) -> str:
    """Hash a token id (``jti``) for storage — the raw id is never persisted."""
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def decode_token(token: str, *, verify_exp: bool = True) -> dict:
    """Decode & verify a JWT. Raises ``jwt.PyJWTError`` on any problem.

    ``verify_exp=False`` skips the expiry check (used by logout to clean up the
    session behind an already-expired but otherwise valid token).
    """
    sec = get_settings().security
    return jwt.decode(
        token,
        sec.secret_key,
        algorithms=[sec.algorithm],
        options={"verify_exp": verify_exp},
    )
