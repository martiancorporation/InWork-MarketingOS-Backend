"""Data access for recorded decisions on AI recommendations.

Every query is hard-filtered by ``client_id`` for tenant isolation. Decisions are
append-only history; the *current* decision for a recommendation is the latest
row for its ``rec_key``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.recommendation import RecommendationAction
from app.repositories.base import BaseRepository


class RecommendationRepository(BaseRepository[RecommendationAction]):
    model = RecommendationAction

    def list_for_client(self, client_id: uuid.UUID) -> list[RecommendationAction]:
        return list(
            self.db.scalars(
                select(RecommendationAction)
                .where(RecommendationAction.client_id == client_id)
                .order_by(RecommendationAction.created_at.desc())
            ).all()
        )

    def latest_by_rec_key(
        self, client_id: uuid.UUID
    ) -> dict[str, RecommendationAction]:
        """Map each rec_key → its most recent decision for this client."""
        latest: dict[str, RecommendationAction] = {}
        # rows come newest-first, so the first seen per rec_key is the latest.
        for row in self.list_for_client(client_id):
            latest.setdefault(row.rec_key, row)
        return latest
