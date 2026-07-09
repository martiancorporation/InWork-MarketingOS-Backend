"""Security primitives: password hashing and JWT access tokens.

Kept free of framework/HTTP concerns so it can be unit-tested in isolation and
reused anywhere. Secrets/algorithm come from settings — never hardcoded.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

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
    subject: str | uuid.UUID, *, expires_minutes: int | None = None
) -> str:
    """Create a signed, short-lived JWT access token for ``subject`` (user id)."""
    sec = get_settings().security
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes or sec.access_token_expire_minutes)
    payload = {
        "sub": str(subject),
        "type": TOKEN_TYPE_ACCESS,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, sec.secret_key, algorithm=sec.algorithm)


def decode_token(token: str) -> dict:
    """Decode & verify a JWT. Raises ``jwt.PyJWTError`` on any problem."""
    sec = get_settings().security
    return jwt.decode(token, sec.secret_key, algorithms=[sec.algorithm])
