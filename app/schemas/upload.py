"""Upload API schemas.

A file is uploaded via ``POST /uploads`` (multipart, proxied to S3). The
response carries the stable ``storage_key`` (what feature tables persist) plus a
short-lived presigned ``download_url``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.common import ORMModel


class UploadRead(ORMModel):
    id: uuid.UUID
    original_filename: str
    content_type: str | None = None
    size_bytes: int
    feature: str | None = None
    storage_key: str
    download_url: str | None = None  # short-lived presigned GET URL
    created_at: datetime
