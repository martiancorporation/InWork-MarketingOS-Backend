"""Client budget schemas — per-period combined + per-platform spend caps.

Mirrors the meeting's ask: one combined monthly figure per client, plus an
optional breakdown per ad platform, fed by the team before each month starts.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel, StrictModel, validate_period

#: Sentinel ``platform`` value for a client's combined/overall budget row.
COMBINED_PLATFORM = "combined"


class BudgetSet(StrictModel):
    period: str = Field(min_length=7, max_length=7, description="YYYY-MM")
    #: "combined" (default) for the overall monthly figure, or a platform key
    #: (e.g. "meta", "google_ads") for that platform's own slice.
    platform: str = Field(default=COMBINED_PLATFORM, max_length=30)
    amount_usd: float = Field(ge=0)

    @field_validator("period")
    @classmethod
    def _validate_period(cls, value: str) -> str:
        return validate_period(value)


class BudgetRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    period: str
    platform: str
    amount_usd: float
    set_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class BudgetPeriodResponse(BaseModel):
    period: str
    combined: BudgetRead | None = None
    by_platform: list[BudgetRead] = []
