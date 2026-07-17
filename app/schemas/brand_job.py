"""Schemas for the async brand-extraction job (transaction-id + poll)."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.common import ORMModel
from app.schemas.onboarding import BrandExtraction


class BrandJobRead(ORMModel):
    id: uuid.UUID  # the transaction id the client polls
    status: str  # pending | running | done | failed
    website: str | None = None
    document_upload_id: uuid.UUID | None = None
    result: BrandExtraction | None = None  # populated when status == "done"
    error: str | None = None  # populated when status == "failed"
    created_at: datetime
    updated_at: datetime
