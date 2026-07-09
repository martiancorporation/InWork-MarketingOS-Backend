"""Unit tests for password hashing and JWT tokens."""

from __future__ import annotations

import uuid

import jwt
import pytest

from app.core.security import (
    TOKEN_TYPE_ACCESS,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    h = hash_password("s3curePass")
    assert h != "s3curePass"
    assert verify_password("s3curePass", h) is True
    assert verify_password("wrong", h) is False


def test_hashes_are_salted():
    assert hash_password("samePass1") != hash_password("samePass1")


def test_long_password_supported():
    # >72 bytes would break raw bcrypt; the sha256 pre-hash handles it.
    long_pw = "a1" * 100
    h = hash_password(long_pw)
    assert verify_password(long_pw, h) is True


def test_access_token_roundtrip():
    uid = uuid.uuid4()
    token = create_access_token(uid)
    payload = decode_token(token)
    assert payload["sub"] == str(uid)
    assert payload["type"] == TOKEN_TYPE_ACCESS


def test_expired_token_is_rejected():
    token = create_access_token(uuid.uuid4(), expires_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)


def test_tampered_token_is_rejected():
    token = create_access_token(uuid.uuid4())
    with pytest.raises(jwt.PyJWTError):
        decode_token(token + "tampered")
