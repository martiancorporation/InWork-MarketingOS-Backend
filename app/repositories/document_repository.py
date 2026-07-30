"""Data access for a client's uploaded document references.

Attaching happens via ``OnboardingService.add_documents`` (append to the ORM
relationship); this repository handles listing and single-row lookups (for
reads and for scoping a delete to the owning client).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.document import Document
from app.models.enums import DocumentKind
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def get_for_client(self, client_id: uuid.UUID, document_id: uuid.UUID) -> Document | None:
        return self.db.scalar(
            select(Document).where(Document.id == document_id, Document.client_id == client_id)
        )

    def list_for_client(
        self,
        client_id: uuid.UUID,
        *,
        kind: DocumentKind | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Document], int]:
        conditions = [Document.client_id == client_id]
        if kind is not None:
            conditions.append(Document.kind == kind)
        total = self.db.scalar(select(func.count()).select_from(Document).where(*conditions))
        stmt = (
            select(Document).where(*conditions).order_by(Document.created_at.desc()).offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)
