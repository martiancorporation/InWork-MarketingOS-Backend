"""Unit tests: OAuth-token encryption at rest."""

from __future__ import annotations

import pytest

from app.core.exceptions import AppError
from app.integrations.crypto import TokenCipher


def test_encrypt_decrypt_round_trip():
    cipher = TokenCipher(secret="a" * 40)
    token = "EAAG-long-lived-meta-token-xyz"
    encrypted = cipher.encrypt(token)
    assert encrypted != token  # actually encrypted, not stored plaintext
    assert cipher.decrypt(encrypted) == token


def test_wrong_key_cannot_decrypt():
    encrypted = TokenCipher(secret="a" * 40).encrypt("secret-token")
    with pytest.raises(AppError):
        TokenCipher(secret="b" * 40).decrypt(encrypted)  # e.g. SECRET_KEY rotated
