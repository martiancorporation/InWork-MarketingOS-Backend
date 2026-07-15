"""Global uploaded-file registry.

One row per file put into object storage (S3). This table is intentionally
*not* tied to any single feature: it is the shared landing zone for every
upload in the app. Feature tables reference an upload by its ``storage_key``
(or resolve it through the upload's id) and tag the origin via ``feature``.

The binary lives in S3; this row holds the metadata and the object key.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    GUID,
    Base,
    JSONColumn,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Upload(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "uploads"

    # Object-storage location.
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)

    # File metadata (filename is sanitized before storage).
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Open string tag for the origin/purpose, e.g. "onboarding.documents",
    # "client.brand", "report". App-defined and grows per feature → plain string.
    feature: Mapped[str | None] = mapped_column(String(80), index=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Loose attribution (e.g. {"client_id": ...}) without a hard FK, so uploads
    # stay decoupled from any one domain table.
    meta: Mapped[dict | None] = mapped_column(JSONColumn)
