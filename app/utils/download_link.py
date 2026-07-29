"""Permanent, signed download links for uploaded files.

An upload's ``download_url`` must survive indefinitely, but S3 presigned URLs
cap out at 7 days even with long-lived IAM keys. The fix is a stable redirect
through our own API: this module signs/verifies an HMAC-SHA256 capability link
to ``GET /uploads/{id}/download`` (see ``app/api/v1/routers/uploads.py``), which
re-presigns against S3 with a short, invisible TTL on every hit. The link
itself never changes; only what it redirects to does.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from urllib.parse import quote

from app.core.config import get_settings


def _digest(value: str) -> str:
    key = get_settings().security.secret_key.encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_upload_id(upload_id: uuid.UUID) -> str:
    """HMAC-SHA256 signature proving a download link was minted by us."""
    return _digest(str(upload_id))


def verify_upload_signature(upload_id: uuid.UUID, signature: str) -> bool:
    """Constant-time check that ``signature`` matches ``upload_id``."""
    return hmac.compare_digest(_digest(str(upload_id)), signature)


def upload_permalink(upload_id: uuid.UUID | str) -> str:
    """Build the permanent, signed download link for an upload.

    Never expires from the caller's perspective — hitting it always 302s to a
    freshly presigned, short-lived S3 URL. Falls back to a relative path when
    ``public_api_base_url`` isn't configured (local/dev), so the link still
    resolves through whatever host actually served it.
    """
    uid = upload_id if isinstance(upload_id, uuid.UUID) else uuid.UUID(str(upload_id))
    sig = sign_upload_id(uid)
    base = (get_settings().app.public_api_base_url or "").rstrip("/")
    return f"{base}/uploads/{uid}/download?sig={sig}"


# ---- legacy bare storage-key fallback ----
#
# Pre-permalink rows can hold a raw storage key with no matching ``uploads`` row
# (the one-off logo repair falls back to this when it can't find one — see
# app/api/v1/routers/clients.py). Namespaced ("key:" prefix) so a key signature
# can never be replayed as a valid upload-id signature or vice versa.


def sign_storage_key(key: str) -> str:
    return _digest(f"key:{key}")


def verify_storage_key_signature(key: str, signature: str) -> bool:
    return hmac.compare_digest(_digest(f"key:{key}"), signature)


def key_permalink(key: str) -> str:
    """Same permanent-link contract as :func:`upload_permalink`, keyed by a raw
    storage key instead of an upload id (no ``uploads`` row to redirect through)."""
    sig = sign_storage_key(key)
    base = (get_settings().app.public_api_base_url or "").rstrip("/")
    return f"{base}/uploads/by-key/download?key={quote(key, safe='')}&sig={sig}"
