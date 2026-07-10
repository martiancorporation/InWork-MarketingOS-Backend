"""AI usage analytics use-cases — detailed log + user/client/platform summaries.

Read-only: writes happen through ``app/ai/usage.py::record_usage`` at call time.
This service only queries and shapes the aggregates for the API.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams
from app.models.ai_usage import AiUsageEvent
from app.repositories.ai_usage_repository import AiUsageRepository, UsageFilters
from app.schemas.ai_usage import (
    AiUsageEventRead,
    AiUsageListResponse,
    ClientUsageSummary,
    DailyUsage,
    PlatformUsageSummary,
    UsageGroupRow,
    UsageTotals,
)


class AiUsageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = AiUsageRepository(db)

    def list(self, f: UsageFilters, pagination: PaginationParams) -> AiUsageListResponse:
        rows, total = self.repo.list(f, offset=pagination.offset, limit=pagination.limit)
        return AiUsageListResponse(
            items=[AiUsageEventRead.model_validate(r) for r in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def platform_summary(self, f: UsageFilters) -> PlatformUsageSummary:
        return PlatformUsageSummary(
            totals=self._totals(f),
            by_feature=self._group(AiUsageEvent.feature, f),
            by_model=self._group(AiUsageEvent.model, f),
            by_client=self._group(AiUsageEvent.client_id, f),
            by_user=self._group(AiUsageEvent.actor_user_id, f),
            daily=self._daily(f),
        )

    def client_summary(self, f: UsageFilters) -> ClientUsageSummary:
        assert f.client_id is not None
        return ClientUsageSummary(
            client_id=f.client_id,
            totals=self._totals(f),
            by_feature=self._group(AiUsageEvent.feature, f),
            by_model=self._group(AiUsageEvent.model, f),
            daily=self._daily(f),
        )

    # ---- shaping helpers ----

    def _totals(self, f: UsageFilters) -> UsageTotals:
        t = self.repo.totals(f)
        return UsageTotals(
            requests=int(t["requests"]),
            input_tokens=int(t["input_tokens"]),
            output_tokens=int(t["output_tokens"]),
            cache_write_tokens=int(t["cache_write_tokens"]),
            cache_read_tokens=int(t["cache_read_tokens"]),
            total_tokens=int(t["total_tokens"]),
            total_cost=float(t["total_cost"]),
        )

    def _group(self, dimension, f: UsageFilters) -> list[UsageGroupRow]:
        return [
            UsageGroupRow(
                key=None if r["key"] is None else str(r["key"]),
                requests=int(r["requests"]),
                total_tokens=int(r["total_tokens"]),
                total_cost=float(r["total_cost"]),
            )
            for r in self.repo.group_totals(dimension, f)
        ]

    def _daily(self, f: UsageFilters) -> list[DailyUsage]:
        return [
            DailyUsage(
                day=str(r["day"]),
                requests=int(r["requests"]),
                total_tokens=int(r["total_tokens"]),
                total_cost=float(r["total_cost"]),
            )
            for r in self.repo.daily(f)
        ]
