"""Symmetric encryption for secrets stored at rest (OAuth access/refresh tokens).

Uses Fernet (AES-128-CBC + HMAC) with a key derived from ``SECRET_KEY`` — no new
secret to manage, and rotating ``SECRET_KEY`` invalidates stored tokens (forcing
a reconnect), which is the safe default. Tokens are encrypted before they touch
the DB and decrypted only when a sync needs to call the provider.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.exceptions import AppError


class TokenCipher:
    def __init__(self, secret: str | None = None) -> None:
        secret = secret or get_settings().security.secret_key
        # 32-byte SHA-256 digest → url-safe base64 → a valid Fernet key.
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:  # wrong key (e.g. SECRET_KEY rotated) / tampered
            raise AppError(
                "Stored credential could not be decrypted — reconnect the integration.",
                code="token_decrypt_failed",
            ) from exc
