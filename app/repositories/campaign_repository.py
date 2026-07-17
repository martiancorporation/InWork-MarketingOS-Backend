"""Data access for campaigns. Every query is hard-filtered by ``client_id``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.models.campaign import Campaign
from app.repositories.base import BaseRepository


class CampaignRepository(BaseRepository[Campaign]):
    model = Campaign

    def get_for_client(
        self, client_id: uuid.UUID, campaign_id: uuid.UUID
    ) -> Campaign | None:
        return self.db.scalar(
            select(Campaign).where(
                Campaign.id == campaign_id, Campaign.client_id == client_id
            )
        )

    def get_many_for_client(
        self, client_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[Campaign]:
        if not ids:
            return []
        return list(
            self.db.scalars(
                select(Campaign).where(
                    Campaign.client_id == client_id, Campaign.id.in_(list(ids))
                )
            ).all()
        )

    def list_for_client(
        self,
        client_id: uuid.UUID,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Campaign], int]:
        conditions = [Campaign.client_id == client_id]
        if status is not None:
            conditions.append(Campaign.status == status)
        total = self.db.scalar(
            select(func.count()).select_from(Campaign).where(*conditions)
        )
        stmt = (
            select(Campaign)
            .where(*conditions)
            .order_by(Campaign.created_at.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)
