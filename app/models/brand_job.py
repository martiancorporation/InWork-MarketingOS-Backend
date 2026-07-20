"""Async brand-extraction job — the transaction-id + poll pattern.

Website scraping / document parsing can exceed a request's safe window (the
">25s API" concern), so the extract-brand call can be run as a background job:
the API returns a job id immediately and the client polls this row for the
result. ``status`` is a plain string (open set: pending / running / done /
failed). Owner-scoped via ``uploaded_by``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin


class BrandJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brand_jobs"

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    website: Mapped[str | None] = mapped_column(String(255))
    document_upload_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    # The BrandExtraction payload once done (JSON), and an error message if failed.
    result: Mapped[dict | None] = mapped_column(JSONColumn)
    error: Mapped[str | None] = mapped_column(Text)
