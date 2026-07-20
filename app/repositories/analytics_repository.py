"""Data access + aggregation for daily analytics facts (hard-filtered by client_id).

Upsert is done row-by-row (find-then-update-or-insert) to stay portable across
Postgres and the SQLite test DB; ingest batches are bounded. Aggregations mirror
the ai-usage repository's coalesce-sum pattern.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.sql import Select

from app.models.analytics import AnalyticsDaily
from app.models.enums import SocialPlatform
from app.repositories.base import BaseRepository

_METRICS = ("impressions", "clicks", "conversions", "leads", "spend", "revenue")


class AnalyticsRepository(BaseRepository[AnalyticsDaily]):
    model = AnalyticsDaily

    def _scope(
        self,
        stmt: Select,
        client_id: uuid.UUID,
        *,
        start: date | None,
        end: date | None,
        platform: SocialPlatform | None,
    ) -> Select:
        stmt = stmt.where(AnalyticsDaily.client_id == client_id)
        if start is not None:
            stmt = stmt.where(AnalyticsDaily.date >= start)
        if end is not None:
            stmt = stmt.where(AnalyticsDaily.date <= end)
        if platform is not None:
            stmt = stmt.where(AnalyticsDaily.platform == platform)
        return stmt

    def get_cell(
        self, client_id: uuid.UUID, day: date, platform: SocialPlatform
    ) -> AnalyticsDaily | None:
        return self.db.scalar(
            select(AnalyticsDaily).where(
                AnalyticsDaily.client_id == client_id,
                AnalyticsDaily.date == day,
                AnalyticsDaily.platform == platform,
            )
        )

    def list_daily(
        self,
        client_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        platform: SocialPlatform | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[AnalyticsDaily], int]:
        """Return a page of daily rows plus the total matching count (DB-side)."""
        total = self.db.scalar(
            self._scope(
                select(func.count()).select_from(AnalyticsDaily),
                client_id,
                start=start,
                end=end,
                platform=platform,
            )
        )
        stmt = (
            self._scope(select(AnalyticsDaily), client_id, start=start, end=end, platform=platform)
            .order_by(AnalyticsDaily.date.asc(), AnalyticsDaily.platform.asc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)

    def totals(
        self,
        client_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        platform: SocialPlatform | None = None,
    ) -> dict:
        cols = [func.coalesce(func.sum(getattr(AnalyticsDaily, m)), 0).label(m) for m in _METRICS]
        stmt = self._scope(select(*cols), client_id, start=start, end=end, platform=platform)
        return dict(self.db.execute(stmt).one()._mapping)

    def by_platform(
        self,
        client_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict]:
        stmt = (
            self._scope(
                select(
                    AnalyticsDaily.platform.label("platform"),
                    func.coalesce(func.sum(AnalyticsDaily.impressions), 0).label("impressions"),
                    func.coalesce(func.sum(AnalyticsDaily.clicks), 0).label("clicks"),
                    func.coalesce(func.sum(AnalyticsDaily.leads), 0).label("leads"),
                    func.coalesce(func.sum(AnalyticsDaily.spend), 0).label("spend"),
                    func.coalesce(func.sum(AnalyticsDaily.revenue), 0).label("revenue"),
                ),
                client_id,
                start=start,
                end=end,
                platform=None,
            )
            .group_by(AnalyticsDaily.platform)
            .order_by(func.sum(AnalyticsDaily.spend).desc())
        )
        return [dict(r._mapping) for r in self.db.execute(stmt).all()]

    def daily_series(
        self,
        client_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        platform: SocialPlatform | None = None,
    ) -> list[dict]:
        stmt = (
            self._scope(
                select(
                    AnalyticsDaily.date.label("date"),
                    func.coalesce(func.sum(AnalyticsDaily.impressions), 0).label("impressions"),
                    func.coalesce(func.sum(AnalyticsDaily.clicks), 0).label("clicks"),
                    func.coalesce(func.sum(AnalyticsDaily.leads), 0).label("leads"),
                    func.coalesce(func.sum(AnalyticsDaily.spend), 0).label("spend"),
                    func.coalesce(func.sum(AnalyticsDaily.revenue), 0).label("revenue"),
                ),
                client_id,
                start=start,
                end=end,
                platform=platform,
            )
            .group_by(AnalyticsDaily.date)
            .order_by(AnalyticsDaily.date.asc())
        )
        return [dict(r._mapping) for r in self.db.execute(stmt).all()]
