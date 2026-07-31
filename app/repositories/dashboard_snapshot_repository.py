"""Data access for the cached AI dashboard bundle (one row per client)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.models.dashboard_snapshot import DashboardSnapshot
from app.repositories.base import BaseRepository


class DashboardSnapshotRepository(BaseRepository[DashboardSnapshot]):
    model = DashboardSnapshot

    def get_for_client(self, client_id: uuid.UUID) -> DashboardSnapshot | None:
        return self.db.scalar(
            select(DashboardSnapshot).where(DashboardSnapshot.client_id == client_id)
        )

    def upsert(
        self,
        client_id: uuid.UUID,
        *,
        payload: dict,
        inputs_hash: str,
        computed_at: datetime,
    ) -> DashboardSnapshot:
        """Get-then-update-or-insert — same idiom as ``AnalyticsService.ingest``."""
        existing = self.get_for_client(client_id)
        if existing is None:
            existing = DashboardSnapshot(client_id=client_id)
            self.add(existing)
        existing.payload = payload
        existing.inputs_hash = inputs_hash
        existing.computed_at = computed_at
        return existing
