"""Data access for recorded client strategies (BE-06).

Every query is hard-filtered by ``client_id``. Strategies are append-only,
versioned snapshots; the *current* strategy is the highest ``version`` for a
client.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.strategy import Strategy
from app.repositories.base import BaseRepository


class StrategyRepository(BaseRepository[Strategy]):
    model = Strategy

    def get_current(self, client_id: uuid.UUID) -> Strategy | None:
        """The latest (highest-version) strategy for a client, if any."""
        return self.db.scalar(
            select(Strategy)
            .where(Strategy.client_id == client_id)
            .order_by(Strategy.version.desc())
            .limit(1)
        )

    def next_version(self, client_id: uuid.UUID) -> int:
        """The version number the next recorded strategy should take (1-based)."""
        current_max = self.db.scalar(
            select(func.max(Strategy.version)).where(Strategy.client_id == client_id)
        )
        return int(current_max or 0) + 1
