"""Data access + aggregation for AI usage events."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.models.ai_usage import AiUsageEvent
from app.repositories.base import BaseRepository


@dataclass(frozen=True)
class UsageFilters:
    client_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    feature: str | None = None
    model: str | None = None
    status: str | None = None
    start: datetime | None = None
    end: datetime | None = None


class AiUsageRepository(BaseRepository[AiUsageEvent]):
    model = AiUsageEvent

    def _apply(self, stmt: Select, f: UsageFilters) -> Select:
        if f.client_id is not None:
            stmt = stmt.where(AiUsageEvent.client_id == f.client_id)
        if f.user_id is not None:
            stmt = stmt.where(AiUsageEvent.actor_user_id == f.user_id)
        if f.feature:
            stmt = stmt.where(AiUsageEvent.feature == f.feature)
        if f.model:
            stmt = stmt.where(AiUsageEvent.model == f.model)
        if f.status:
            stmt = stmt.where(AiUsageEvent.status == f.status)
        if f.start is not None:
            stmt = stmt.where(AiUsageEvent.created_at >= f.start)
        if f.end is not None:
            stmt = stmt.where(AiUsageEvent.created_at <= f.end)
        return stmt

    def list(self, f: UsageFilters, *, offset: int, limit: int) -> tuple[list[AiUsageEvent], int]:
        base = self._apply(select(AiUsageEvent), f)
        total = int(self.db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = list(
            self.db.scalars(
                base.order_by(AiUsageEvent.created_at.desc(), AiUsageEvent.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return rows, total

    def totals(self, f: UsageFilters) -> dict:
        cols = (
            func.count().label("requests"),
            func.coalesce(func.sum(AiUsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AiUsageEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AiUsageEvent.cache_write_tokens), 0).label("cache_write_tokens"),
            func.coalesce(func.sum(AiUsageEvent.cache_read_tokens), 0).label("cache_read_tokens"),
            func.coalesce(func.sum(AiUsageEvent.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AiUsageEvent.total_cost), 0).label("total_cost"),
        )
        stmt = self._apply(select(*cols), f)
        row = self.db.execute(stmt).one()
        return dict(row._mapping)

    def group_totals(
        self, dimension: ColumnElement, f: UsageFilters, *, limit: int = 50
    ) -> list[dict]:
        stmt = (
            self._apply(
                select(
                    dimension.label("key"),
                    func.count().label("requests"),
                    func.coalesce(func.sum(AiUsageEvent.total_tokens), 0).label("total_tokens"),
                    func.coalesce(func.sum(AiUsageEvent.total_cost), 0).label("total_cost"),
                ),
                f,
            )
            .group_by(dimension)
            .order_by(func.sum(AiUsageEvent.total_cost).desc())
            .limit(limit)
        )
        return [dict(r._mapping) for r in self.db.execute(stmt).all()]

    def by_feature_model(self, f: UsageFilters, *, limit: int = 200) -> list[dict]:
        """Per-(feature, model) rollup used by cost-optimization heuristics."""
        stmt = (
            self._apply(
                select(
                    AiUsageEvent.feature.label("feature"),
                    AiUsageEvent.model.label("model"),
                    func.count().label("requests"),
                    func.coalesce(func.sum(AiUsageEvent.input_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(AiUsageEvent.output_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(AiUsageEvent.cache_read_tokens), 0).label(
                        "cache_read_tokens"
                    ),
                    func.coalesce(func.sum(AiUsageEvent.total_tokens), 0).label("total_tokens"),
                    func.coalesce(func.sum(AiUsageEvent.total_cost), 0).label("total_cost"),
                ),
                f,
            )
            .group_by(AiUsageEvent.feature, AiUsageEvent.model)
            .order_by(func.sum(AiUsageEvent.total_cost).desc())
            .limit(limit)
        )
        return [dict(r._mapping) for r in self.db.execute(stmt).all()]

    def daily(self, f: UsageFilters, *, limit: int = 90) -> list[dict]:
        day = func.date(AiUsageEvent.created_at).label("day")
        stmt = (
            self._apply(
                select(
                    day,
                    func.count().label("requests"),
                    func.coalesce(func.sum(AiUsageEvent.total_tokens), 0).label("total_tokens"),
                    func.coalesce(func.sum(AiUsageEvent.total_cost), 0).label("total_cost"),
                ),
                f,
            )
            .group_by(day)
            .order_by(day.desc())
            .limit(limit)
        )
        return [dict(r._mapping) for r in self.db.execute(stmt).all()]
