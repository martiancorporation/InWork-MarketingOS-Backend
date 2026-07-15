"""Data access for compliance-register entries (hard-filtered by client_id)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.compliance import ComplianceEntry
from app.models.enums import ComplianceKind
from app.repositories.base import BaseRepository


class ComplianceRepository(BaseRepository[ComplianceEntry]):
    model = ComplianceEntry

    def get_for_client(
        self, client_id: uuid.UUID, entry_id: uuid.UUID
    ) -> ComplianceEntry | None:
        return self.db.scalar(
            select(ComplianceEntry).where(
                ComplianceEntry.id == entry_id,
                ComplianceEntry.client_id == client_id,
            )
        )

    def list_for_client(
        self,
        client_id: uuid.UUID,
        *,
        kind: ComplianceKind | None = None,
        active_only: bool = False,
    ) -> list[ComplianceEntry]:
        stmt = select(ComplianceEntry).where(ComplianceEntry.client_id == client_id)
        if kind is not None:
            stmt = stmt.where(ComplianceEntry.kind == kind)
        if active_only:
            stmt = stmt.where(ComplianceEntry.is_active.is_(True))
        stmt = stmt.order_by(ComplianceEntry.created_at.desc())
        return list(self.db.scalars(stmt).all())
