"""Data access for the intelligence job queue."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.enums import IntelJobStatus
from app.models.intel_job import IntelJob
from app.repositories.base import BaseRepository


class IntelJobRepository(BaseRepository[IntelJob]):
    model = IntelJob

    def pending_for_client(self, client_id: uuid.UUID) -> IntelJob | None:
        """An existing queued job for this client (for coalescing enqueues)."""
        return self.db.scalar(
            select(IntelJob)
            .where(
                IntelJob.client_id == client_id,
                IntelJob.status == IntelJobStatus.queued.value,
            )
            .order_by(IntelJob.created_at)
            .limit(1)
        )

    def latest_for_client(self, client_id: uuid.UUID) -> IntelJob | None:
        return self.db.scalar(
            select(IntelJob)
            .where(IntelJob.client_id == client_id)
            .order_by(IntelJob.created_at.desc())
            .limit(1)
        )
