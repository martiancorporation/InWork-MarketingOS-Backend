"""Data access for per-client budgets (hard-filtered by client_id)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.budget import ClientBudget
from app.repositories.base import BaseRepository


class BudgetRepository(BaseRepository[ClientBudget]):
    model = ClientBudget

    def get_for_period_platform(
        self, client_id: uuid.UUID, period: str, platform: str
    ) -> ClientBudget | None:
        return self.db.scalar(
            select(ClientBudget).where(
                ClientBudget.client_id == client_id,
                ClientBudget.period == period,
                ClientBudget.platform == platform,
            )
        )

    def list_for_period(self, client_id: uuid.UUID, period: str) -> list[ClientBudget]:
        stmt = (
            select(ClientBudget)
            .where(ClientBudget.client_id == client_id, ClientBudget.period == period)
            .order_by(ClientBudget.platform)
        )
        return list(self.db.scalars(stmt).all())
