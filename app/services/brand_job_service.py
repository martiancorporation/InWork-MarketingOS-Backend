"""Async brand-extraction jobs — create, poll, and background processing.

The API creates a ``pending`` job and returns its id (the transaction id); the
work runs in the background (``process``) on its own DB session so it survives
past the HTTP response, and the client polls ``get`` for the result. Owner-scoped
(admin sees all; others only their own → 404).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.ai.brand_extraction import BrandExtractionService
from app.ai.features import AiFeature
from app.ai.usage import AiUsageContext
from app.api.deps import get_storage
from app.core.exceptions import NotFoundError
from app.db.session import get_session_factory
from app.integrations.storage import Storage
from app.models.brand_job import BrandJob
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.brand_job_repository import BrandJobRepository
from app.schemas.onboarding import BrandExtractionRequest
from app.services.upload_service import UploadService

logger = logging.getLogger("app.services.brand_job")


class BrandJobService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.jobs = BrandJobRepository(db)

    def create(self, user: User, data: BrandExtractionRequest) -> BrandJob:
        job = self.jobs.create(
            user_id=user.id,
            website=data.website,
            document_upload_id=data.document_upload_id,
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def get(self, user: User, job_id: uuid.UUID) -> BrandJob:
        job = self.jobs.get(job_id)
        if job is None or (user.role != UserRole.admin and job.uploaded_by != user.id):
            raise NotFoundError("Brand job not found.")
        return job

    @staticmethod
    async def process(
        job_id: uuid.UUID,
        *,
        session: Session | None = None,
        storage: Storage | None = None,
    ) -> None:
        """Run one brand job to completion. Uses its own session (background) unless
        one is injected (tests). Never raises — failures are recorded on the row."""
        own = session is None
        db = session or get_session_factory()()
        try:
            try:
                job = db.get(BrandJob, job_id)
                if job is None:
                    return
                job.status = "running"
                db.commit()
                result = await BrandJobService._run_extraction(db, job, storage)
                job.result = result.model_dump()
                job.status = "done"
                db.commit()
            except Exception as exc:  # noqa: BLE001 — a bad job must not crash the worker
                logger.warning("Brand job %s failed", job_id, exc_info=True)
                db.rollback()
                job = db.get(BrandJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error = str(exc)[:500]
                    db.commit()
        except Exception:  # background run against an unavailable DB — swallow entirely
            logger.exception("Brand job %s could not be processed", job_id)
        finally:
            if own:
                db.close()

    @staticmethod
    async def _run_extraction(db: Session, job: BrandJob, storage: Storage | None):
        data = BrandExtractionRequest(
            website=job.website, document_upload_id=job.document_upload_id
        )
        document = None
        if job.document_upload_id is not None:
            store = storage or get_storage()
            owner = db.get(User, job.uploaded_by) if job.uploaded_by else None
            if owner is None:
                raise NotFoundError("Job owner no longer exists.")
            raw, content_type, filename = UploadService(db, store).read_bytes(
                owner, job.document_upload_id
            )
            document = BrandExtractionService.document_from_bytes(raw, content_type, filename)
        context = AiUsageContext(
            feature=AiFeature.BRAND_EXTRACTION,
            user_id=job.uploaded_by,
            meta={"brand_job_id": str(job.id)},
        )
        return await BrandExtractionService().extract(data, context, document=document)
