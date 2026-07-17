"""Data access for async brand-extraction jobs."""

from __future__ import annotations

import uuid

from app.models.brand_job import BrandJob
from app.repositories.base import BaseRepository


class BrandJobRepository(BaseRepository[BrandJob]):
    model = BrandJob

    def create(
        self,
        *,
        user_id: uuid.UUID | None,
        website: str | None,
        document_upload_id: uuid.UUID | None,
    ) -> BrandJob:
        job = BrandJob(
            uploaded_by=user_id,
            status="pending",
            website=website,
            document_upload_id=document_upload_id,
        )
        self.db.add(job)
        self.db.flush()
        return job
