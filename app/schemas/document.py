"""Read schema for a client's uploaded document references.

Writes go through the onboarding flow (``DocumentRef``/``DocumentsRequest`` in
``app/schemas/onboarding.py``); this is the list/read side.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.enums import DocumentKind
from app.schemas.common import ORMModel
from app.utils.download_link import key_permalink


class DocumentRead(ORMModel):
    id: uuid.UUID
    kind: DocumentKind
    name: str
    mime_type: str | None = None
    size_bytes: int
    storage_url: str
    uploaded_by: uuid.UUID | None = None
    created_at: datetime

    @field_validator("storage_url")
    @classmethod
    def _resolve_storage_url(cls, value: str) -> str:
        """Despite the column's name, this holds a bare S3 storage key (the
        frontend sends ``file.storage_key``, never a presigned URL) — resolve it
        into a permanent, signed download link. Passthrough for the rare
        genuinely-external URL, same convention as ``resolve_logo_url``."""
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return key_permalink(value)


class DocumentListResponse(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int = 1
    page_size: int = 20
