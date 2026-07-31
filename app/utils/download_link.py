"""Permanent, signed download links for uploaded files.

An upload's ``download_url`` must survive indefinitely, but S3 presigned URLs
cap out at 7 days even with long-lived IAM keys. The fix is a stable redirect
through our own API: this module signs/verifies an HMAC-SHA256 capability link
to ``GET /uploads/{id}/download`` (see ``app/api/v1/routers/uploads.py``), which
re-presigns against S3 with a short, invisible TTL on every hit. The link
itself never changes; only what it redirects to does.

The signature is scoped to the upload's current ``link_epoch`` (see
``app/models/upload.py``), so a specific leaked link can be revoked —
``UploadService.regenerate_link`` bumps the epoch, which invalidates every
signature issued before the bump, without deleting the file or forcing every
other upload's links to expire too.
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


def _upload_sign_input(upload_id: uuid.UUID, epoch: int) -> str:
    """The exact string signed for ``upload_id`` at ``epoch``.

    Epoch 0 signs the bare id — byte-identical to every link issued before
    ``link_epoch`` existed — so upgrading to this module never invalidates a
    link (or a permalink already persisted in a DB row, e.g. ``Report.file_url``,
    or a client logo) that was minted under the old, epoch-less scheme. Only a
    *bumped* epoch (``UploadService.regenerate_link``) changes the signed
    input, which is exactly what should invalidate the old signature.

    PRODUCTION INCIDENT (see git history / conversation log): a prior deploy
    shipped this module signing ``f"{upload_id}:{epoch}"`` unconditionally,
    including at epoch 0 — the default for every upload that has never been
    through regenerate-link, i.e. effectively all of them. That broke every
    pre-existing signed link (client logos, chat attachments, stored
    ``Report.file_url`` rows) the moment it went live, since old links were
    signed over the bare id with no epoch suffix at all. Do not remove the
    epoch==0 special case below.
    """
    return str(upload_id) if epoch == 0 else f"{upload_id}:{epoch}"


def sign_upload_id(upload_id: uuid.UUID, *, epoch: int = 0) -> str:
    """HMAC-SHA256 signature proving a download link was minted by us for the
    upload's current ``link_epoch``."""
    return _digest(_upload_sign_input(upload_id, epoch))


def verify_upload_signature(upload_id: uuid.UUID, signature: str, *, epoch: int = 0) -> bool:
    """Constant-time check that ``signature`` matches ``upload_id`` at ``epoch``."""
    return hmac.compare_digest(_digest(_upload_sign_input(upload_id, epoch)), signature)


def upload_permalink(upload_id: uuid.UUID | str, *, epoch: int = 0) -> str:
    """Build the permanent, signed download link for an upload.

    Never expires from the caller's perspective — hitting it always 302s to a
    freshly presigned, short-lived S3 URL — unless its ``link_epoch`` is bumped
    (revocation), which invalidates links signed for an earlier epoch. Falls
    back to a relative path when ``public_api_base_url`` isn't configured
    (local/dev), so the link still resolves through whatever host actually
    served it.
    """
    uid = upload_id if isinstance(upload_id, uuid.UUID) else uuid.UUID(str(upload_id))
    sig = sign_upload_id(uid, epoch=epoch)
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
