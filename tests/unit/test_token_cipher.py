"""Unit tests: OAuth-token encryption at rest."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
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


def test_falls_back_to_secret_key_when_encryption_key_unset(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    default_cipher = TokenCipher()
    same_as_secret = TokenCipher(secret=get_settings().security.secret_key)
    token = "some-token"
    assert same_as_secret.decrypt(default_cipher.encrypt(token)) == token
    get_settings.cache_clear()


def test_encryption_key_decouples_from_secret_key(monkeypatch):
    """Rotating SECRET_KEY must not invalidate tokens encrypted under a
    separately-configured ENCRYPTION_KEY."""
    original_secret = get_settings().security.secret_key
    try:
        monkeypatch.setenv("ENCRYPTION_KEY", "c" * 40)
        get_settings.cache_clear()
        encrypted = TokenCipher().encrypt("secret-token")

        monkeypatch.setenv("SECRET_KEY", "d" * 40)  # simulate a JWT-key rotation
        get_settings.cache_clear()
        assert TokenCipher().decrypt(encrypted) == "secret-token"
    finally:
        # Restore before the cache is rebuilt so later tests see the original
        # hermetic settings, regardless of monkeypatch's teardown ordering.
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", original_secret)
        get_settings.cache_clear()
