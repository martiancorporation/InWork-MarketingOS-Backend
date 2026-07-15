"""Global file API (Amazon S3 backed).

Reusable across the whole app — any feature stores files here and references the
returned ``storage_key``. Three endpoints:

- ``POST /uploads``            — upload a file (multipart, proxied to S3)
- ``GET /uploads/{id}``        — file metadata + a fresh presigned download URL
- ``DELETE /uploads/{id}``     — delete the object and its record

All require auth; a user sees only their own files, an admin sees all.
"""

from __future__ import annotations

import uuid
from functools import partial

import anyio
from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import CurrentUser, DbSession, StorageDep
from app.core.config import get_settings
from app.core.exceptions import PayloadTooLargeError
from app.schemas.common import MessageResponse
from app.schemas.upload import UploadRead
from app.services.upload_service import UploadService

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
            raise PayloadTooLargeError(
                f"File exceeds the {max_bytes // (1024 * 1024)} MB limit."
            )
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
