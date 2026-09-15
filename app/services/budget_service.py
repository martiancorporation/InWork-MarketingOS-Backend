"""Client budget use-cases: read a period's figures, upsert one row.

Client-access scoping is enforced at the router (via ``RequireClient``/
``require_capability``) before any method here runs. Repository flushes only;
this service owns the commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.models.budget import ClientBudget
from app.repositories.budget_repository import BudgetRepository
from app.schemas.budget import (
    COMBINED_PLATFORM,
    BudgetPeriodResponse,
    BudgetRead,
    BudgetSet,
    validate_period,
)


class BudgetService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.budgets = BudgetRepository(db)

    def get_period(self, client_id: uuid.UUID, period: str) -> BudgetPeriodResponse:
        try:
            validate_period(period)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

        rows = self.budgets.list_for_period(client_id, period)
        combined = next((r for r in rows if r.platform == COMBINED_PLATFORM), None)
        by_platform = [r for r in rows if r.platform != COMBINED_PLATFORM]
        return BudgetPeriodResponse(
            period=period,
            combined=BudgetRead.model_validate(combined) if combined else None,
            by_platform=[BudgetRead.model_validate(r) for r in by_platform],
        )

    def set_budget(
        self, client_id: uuid.UUID, data: BudgetSet, *, set_by: uuid.UUID
    ) -> ClientBudget:
        existing = self.budgets.get_for_period_platform(client_id, data.period, data.platform)
        if existing is not None:
            existing.amount_usd = data.amount_usd
            existing.set_by = set_by
            row = existing
        else:
            row = ClientBudget(
                client_id=client_id,
                period=data.period,
                platform=data.platform,
                amount_usd=data.amount_usd,
                set_by=set_by,
            )
            self.budgets.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row
