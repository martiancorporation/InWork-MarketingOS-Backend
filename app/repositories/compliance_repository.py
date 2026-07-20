"""Data access for compliance-register entries (hard-filtered by client_id)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.compliance import ComplianceEntry
from app.models.enums import ComplianceKind
from app.repositories.base import BaseRepository


class ComplianceRepository(BaseRepository[ComplianceEntry]):
    model = ComplianceEntry

    def get_for_client(self, client_id: uuid.UUID, entry_id: uuid.UUID) -> ComplianceEntry | None:
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
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[ComplianceEntry], int]:
        """Return a page of entries plus the total matching count (DB-side)."""
        conditions = [ComplianceEntry.client_id == client_id]
        if kind is not None:
            conditions.append(ComplianceEntry.kind == kind)
        if active_only:
            conditions.append(ComplianceEntry.is_active.is_(True))

        total = self.db.scalar(select(func.count()).select_from(ComplianceEntry).where(*conditions))
        stmt = (
            select(ComplianceEntry)
            .where(*conditions)
            .order_by(ComplianceEntry.created_at.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)
