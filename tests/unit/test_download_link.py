"""Unit tests for the permanent, signed upload-download link helpers.

``PUBLIC_API_BASE_URL`` is pinned to a fixed test value in ``conftest.py`` so the
built links are deterministic absolute URLs.
"""

from __future__ import annotations

import uuid

from app.utils.download_link import (
    key_permalink,
    sign_upload_id,
    upload_permalink,
    verify_storage_key_signature,
    verify_upload_signature,
)


def test_upload_permalink_shape() -> None:
    upload_id = uuid.uuid4()
    url = upload_permalink(upload_id)
    assert url.startswith("https://api.test.example/api/v1/uploads/")
    assert f"/uploads/{upload_id}/download?sig=" in url


def test_sign_and_verify_round_trip() -> None:
    upload_id = uuid.uuid4()
    sig = sign_upload_id(upload_id)
    assert verify_upload_signature(upload_id, sig)


def test_verify_rejects_tampered_signature() -> None:
    upload_id = uuid.uuid4()
    sig = sign_upload_id(upload_id)
    assert not verify_upload_signature(upload_id, sig[:-1] + ("0" if sig[-1] != "0" else "1"))


def test_verify_rejects_signature_for_a_different_id() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert not verify_upload_signature(b, sign_upload_id(a))


def test_upload_id_signature_and_key_signature_are_not_interchangeable() -> None:
    """Namespacing: an id's signature must not verify as a key's signature, even
    when the id's string form happens to equal the key (astronomically unlikely,
    but the namespace prefix means it can never happen by construction)."""
    upload_id = uuid.uuid4()
    id_sig = sign_upload_id(upload_id)
    assert not verify_storage_key_signature(str(upload_id), id_sig)


def test_key_permalink_round_trip() -> None:
    key = "uploads/legacy-no-upload-row/logo.png"
    url = key_permalink(key)
    assert url.startswith("https://api.test.example/api/v1/uploads/by-key/download?key=")
    assert "sig=" in url


def test_key_permalink_tampered_signature_rejected() -> None:
    key = "uploads/some/key.png"
    url = key_permalink(key)
    sig = url.split("sig=")[1]
    assert not verify_storage_key_signature(key, sig[:-1] + ("0" if sig[-1] != "0" else "1"))
