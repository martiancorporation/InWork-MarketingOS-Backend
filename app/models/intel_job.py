"""Durable job queue for the async client-intelligence pipeline.

Rows are claimed by the worker with ``SELECT ... FOR UPDATE SKIP LOCKED`` so
multiple workers scale horizontally. One in-flight job per client is enforced in
the service layer to avoid profile-version races.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    GUID,
    Base,
    JSONColumn,
    TimestampMixin,
    TZDateTime,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import IntelJobStatus, IntelJobType


class IntelJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "intel_jobs"
    __table_args__ = (
        Index("ix_intel_jobs_claim", "status", "run_after"),
        Index("ix_intel_jobs_client_status", "client_id", "status"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IntelJobType.full_build.value
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IntelJobStatus.queued.value, index=True
    )
    # For incremental jobs: which source keys changed. Null/empty → rebuild all.
    payload: Mapped[dict | None] = mapped_column(JSONColumn)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_after: Mapped[datetime | None] = mapped_column(TZDateTime)  # debounce
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
