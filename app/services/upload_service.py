"""File-upload use-cases — the reusable, app-wide storage entry point.

Any feature attaches files by going through this service. It validates
(content-type allow-list, size limit, filename sanitization), streams the bytes
to the injected ``Storage`` backend (S3), and records each object in the
``uploads`` table. Downloads are served as short-lived presigned URLs, so stored
objects stay private.

Repositories never commit; this service owns the transaction boundary.
"""

from __future__ import annotations

import os
import re
import uuid
from io import BytesIO

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.config.storage import StorageSettings
from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.integrations.storage import Storage
from app.models.enums import UserRole
from app.models.upload import Upload
from app.models.user import User
from app.repositories.upload_repository import UploadRepository
from app.schemas.upload import UploadRead

_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_filename(name: str) -> str:
    """Strip any path and reduce to a safe basename (defeats path traversal)."""
    base = os.path.basename((name or "").replace("\\", "/").strip()).split("/")[-1]
    base = _FILENAME_UNSAFE.sub("_", base).lstrip(".")
    return (base or "file")[:255]


class UploadService:
    def __init__(
        self,
        db: Session,
        storage: Storage,
        settings: StorageSettings | None = None,
    ) -> None:
        self.db = db
        self.storage = storage
        self.settings = settings or get_settings().storage
        self.uploads = UploadRepository(db)

    # ---- validation ----

    def _validate(self, filename: str, content_type: str, size_bytes: int) -> None:
        if not (filename or "").strip():
            raise BadRequestError("A filename is required.")
        if size_bytes <= 0:
            raise BadRequestError("File size must be greater than zero.")
        if size_bytes > self.settings.max_upload_bytes:
            raise PayloadTooLargeError(
                f"File exceeds the {self.settings.max_upload_mb} MB limit."
            )
        if not self.settings.allows_content_type(content_type):
            raise UnsupportedMediaTypeError(
                f"Content type '{content_type}' is not allowed."
            )

    def _build_key(self, upload_id: uuid.UUID, safe_filename: str) -> str:
        prefix = self.settings.key_prefix.strip("/")
        return f"{prefix}/{upload_id}/{safe_filename}"

    # ---- upload ----

    def store_bytes(
        self,
        user: User,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        feature: str | None = None,
        meta: dict | None = None,
    ) -> UploadRead:
        self._validate(filename, content_type, len(data))
        upload_id = uuid.uuid4()
        safe = sanitize_filename(filename)
        key = self._build_key(upload_id, safe)

        # Push to storage before committing the row; on failure nothing persists.
        self.storage.upload(BytesIO(data), key, content_type)

        upload = Upload(
            id=upload_id,
            bucket=self.settings.s3_bucket or "",
            storage_key=key,
            original_filename=safe,
            content_type=content_type,
            size_bytes=len(data),
            feature=feature,
            uploaded_by=user.id,
            meta=meta,
        )
        self.uploads.add(upload)
        try:
            self._commit("Could not record the upload.")
        except AppError:
            # Roll back the orphaned object so storage and DB stay consistent.
            self._safe_delete(key)
            raise
        return self._to_read(upload)

    # ---- reads / delete ----

    def get(self, user: User, upload_id: uuid.UUID) -> UploadRead:
        return self._to_read(self._load_owned(user, upload_id))

    def delete(self, user: User, upload_id: uuid.UUID) -> None:
        upload = self._load_owned(user, upload_id)
        self._safe_delete(upload.storage_key)  # best-effort; still drop the row
        self.db.delete(upload)
        self._commit("Could not delete the upload.")

    # ---- helpers ----

    def _load_owned(self, user: User, upload_id: uuid.UUID) -> Upload:
        upload = self.uploads.get(upload_id)
        # 404 (not 403) for someone else's upload so ids can't be probed.
        if upload is None or (
            user.role != UserRole.admin and upload.uploaded_by != user.id
        ):
            raise NotFoundError("Upload not found.")
        return upload

    def _to_read(self, upload: Upload) -> UploadRead:
        read = UploadRead.model_validate(upload)
        try:
            read.download_url = self.storage.generate_download_url(
                upload.storage_key, self.settings.presign_expiry_seconds
            )
        except AppError:
            read.download_url = None
        return read

    def _safe_delete(self, key: str) -> None:
        try:
            self.storage.delete(key)
        except AppError:
            pass

    def _commit(self, error_message: str) -> None:
        try:
            self.db.commit()
        except Exception as exc:  # pragma: no cover - unique-key race etc.
            self.db.rollback()
            raise ConflictError(error_message) from exc
