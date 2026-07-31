"""Unit tests for the permanent, signed upload-download link helpers.

``PUBLIC_API_BASE_URL`` is pinned to a fixed test value in ``conftest.py`` so the
built links are deterministic absolute URLs.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid

from app.core.config import get_settings
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


def test_epoch_0_is_byte_compatible_with_pre_epoch_links() -> None:
    """Every link ever issued before ``link_epoch`` existed was signed over the
    bare upload id, with no epoch marker at all. Upgrading to this module must
    not invalidate those — including ones already persisted in a DB row (e.g.
    ``Report.file_url``) or shown as a client logo — which nothing here has a
    chance to re-sign.

    Regression test: a prior deploy signed ``f"{upload_id}:{epoch}"``
    unconditionally (even at epoch 0, the default for every upload), which
    broke every pre-existing image/logo/attachment link in production the
    moment it went live. Do not remove the epoch==0 special case in
    ``app/utils/download_link.py``.
    """
    upload_id = uuid.uuid4()
    key = get_settings().security.secret_key.encode("utf-8")
    pre_epoch_signature = hmac.new(key, str(upload_id).encode("utf-8"), hashlib.sha256).hexdigest()

    assert sign_upload_id(upload_id, epoch=0) == pre_epoch_signature
    assert verify_upload_signature(upload_id, pre_epoch_signature, epoch=0)


def test_bumping_the_epoch_still_invalidates_the_old_signature() -> None:
    """The revocation feature (UploadService.regenerate_link) must still work:
    a signature minted at epoch 0 must fail once the epoch is bumped."""
    upload_id = uuid.uuid4()
    old_signature = sign_upload_id(upload_id, epoch=0)

    assert not verify_upload_signature(upload_id, old_signature, epoch=1)
    assert verify_upload_signature(upload_id, sign_upload_id(upload_id, epoch=1), epoch=1)
