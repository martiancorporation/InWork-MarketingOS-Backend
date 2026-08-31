"""Data access for ``AnalyticsBreakdown`` (GA4/Search Console dimensional
slices — top pages, channels, devices, search queries).

Each sync is a full snapshot replacement (delete then insert), not an
upsert — see ``replace_breakdowns``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, insert, select

from app.models.analytics_breakdown import AnalyticsBreakdown
from app.repositories.base import BaseRepository


class AnalyticsBreakdownRepository(BaseRepository[AnalyticsBreakdown]):
    model = AnalyticsBreakdown

    def replace_breakdowns(
        self, client_id: uuid.UUID, integration_key: str, rows: list[dict[str, Any]]
    ) -> int:
        """Sync writes the *current* top-N per type — a dimension that fell out
        of the top-N (e.g. a page no longer in the top 10) should disappear,
        not linger from a previous sync. Delete this integration's existing
        rows first, then insert the fresh set.

        ``rows``: ``{"breakdown_type", "dimension", "rank", "metrics"}``."""
        self.db.query(AnalyticsBreakdown).filter(
            AnalyticsBreakdown.client_id == client_id,
            AnalyticsBreakdown.integration_key == integration_key,
        ).delete(synchronize_session=False)
        if not rows:
            return 0
        dedup: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            dedup[(row["breakdown_type"], row["dimension"])] = {
                "id": uuid.uuid4(),
                "client_id": client_id,
                "integration_key": integration_key,
                "breakdown_type": row["breakdown_type"],
                "dimension": row["dimension"],
                "rank": row.get("rank", 0),
                "metrics": row.get("metrics") or {},
            }
        self.db.execute(insert(AnalyticsBreakdown).values(list(dedup.values())))
        return len(dedup)

    def list_breakdowns(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        breakdown_type: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[AnalyticsBreakdown], int]:
        stmt = select(AnalyticsBreakdown).where(
            AnalyticsBreakdown.client_id == client_id,
            AnalyticsBreakdown.integration_key == integration_key,
        )
        if breakdown_type:
            stmt = stmt.where(AnalyticsBreakdown.breakdown_type == breakdown_type)
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = stmt.order_by(AnalyticsBreakdown.breakdown_type, AnalyticsBreakdown.rank).offset(
            offset
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)
