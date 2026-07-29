"""Global file API (Amazon S3 backed).

Reusable across the whole app — any feature stores files here and references the
returned ``storage_key``. Endpoints:

- ``POST /uploads``               — upload a file (multipart, proxied to S3)
- ``GET /uploads/{id}``           — file metadata + a permanent download link
- ``GET /uploads/{id}/download``  — unauthenticated, signature-gated redirect to
  a freshly presigned, short-lived S3 URL (what the permanent link above points
  at — see ``app/utils/download_link.py``)
- ``DELETE /uploads/{id}``        — delete the object and its record

The metadata/delete routes require auth; a user sees only their own files, an
admin sees all. The ``/download`` redirect deliberately has **no** bearer auth
(browsers can't attach an ``Authorization`` header to ``<img src>``/``<a href>``)
— possession of a validly-signed link is the authorization, same trade-off as a
Slack/Dropbox share link. Deleting the upload still revokes it (404s once the
row is gone).
"""

from __future__ import annotations

import uuid
from functools import partial

import anyio
from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, DbSession, StorageDep
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, PayloadTooLargeError
from app.repositories.upload_repository import UploadRepository
from app.schemas.common import MessageResponse
from app.schemas.upload import UploadRead
from app.services.upload_service import UploadService
from app.utils.download_link import verify_storage_key_signature, verify_upload_signature

router = APIRouter(prefix="/uploads", tags=["uploads"])

_READ_CHUNK = 1024 * 1024  # 1 MB


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile into memory, bounded by ``max_bytes`` so a hostile
    client can't exhaust memory."""
    buffer = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise PayloadTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    return bytes(buffer)


@router.post(
    "",
    response_model=UploadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
)
async def upload_file(
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    file: UploadFile = File(...),
    feature: str | None = Form(default=None),
) -> UploadRead:
    max_bytes = get_settings().storage.max_upload_bytes
    data = await _read_capped(file, max_bytes)
    # store_bytes does a blocking S3 PUT + DB commit; run it off the event loop
    # so it doesn't stall every other concurrent request on this worker.
    return await anyio.to_thread.run_sync(
        partial(
            UploadService(db, storage).store_bytes,
            user,
            filename=file.filename or "file",
            content_type=file.content_type or "application/octet-stream",
            data=data,
            feature=feature,
        )
    )


@router.get("/{upload_id}", response_model=UploadRead, summary="Get a file")
def get_upload(
    upload_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> UploadRead:
    return UploadService(db, storage).get(user, upload_id)


@router.get(
    "/by-key/download",
    include_in_schema=False,
    summary="Signed redirect keyed by a raw storage key (legacy, no uploads row)",
)
def download_by_key(
    db: DbSession, storage: StorageDep, key: str = Query(...), sig: str = Query(...)
) -> RedirectResponse:
    """Same contract as ``download_upload`` below, for the rare pre-permalink row
    that holds a bare storage key with no matching ``uploads`` record (see
    ``resolve_logo_url`` in ``app/schemas/client.py``). Must be registered before
    ``/{upload_id}/download`` — both are two-segment paths and Starlette matches
    registration order, not specificity.
    """
    if not verify_storage_key_signature(key, sig):
        raise NotFoundError("File not found.")
    url = storage.generate_download_url(key, get_settings().storage.presign_expiry_seconds)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/{upload_id}/download",
    include_in_schema=False,
    summary="Signed redirect to a freshly presigned S3 URL (no auth — see module docstring)",
)
def download_upload(
    upload_id: uuid.UUID, db: DbSession, storage: StorageDep, sig: str = Query(...)
) -> RedirectResponse:
    # Same 404 for a bad signature and a missing/deleted upload — neither leaks
    # which one it was, so a signature can't be probed for a valid id.
    if not verify_upload_signature(upload_id, sig):
        raise NotFoundError("Upload not found.")
    upload = UploadRepository(db).get(upload_id)
    if upload is None:
        raise NotFoundError("Upload not found.")
    url = storage.generate_download_url(
        upload.storage_key, get_settings().storage.presign_expiry_seconds
    )
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.delete(
    "/{upload_id}",
    response_model=MessageResponse,
    summary="Delete a file (object + record)",
)
def delete_upload(
    upload_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> MessageResponse:
    UploadService(db, storage).delete(user, upload_id)
    return MessageResponse(detail="Upload deleted.")
