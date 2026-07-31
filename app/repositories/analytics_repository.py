"""Data access + aggregation for daily analytics facts (hard-filtered by client_id).

Upsert is a single dialect-native ``INSERT ... ON CONFLICT DO UPDATE`` statement
(Postgres and SQLite both support the standard syntax) keyed on the
``(client_id, date, platform)`` unique constraint, so ingesting an N-row batch
costs one round trip instead of up to ``2N``. Aggregations mirror the ai-usage
repository's coalesce-sum pattern.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql import Select

from app.models.analytics import AnalyticsDaily
from app.models.enums import SocialPlatform
from app.repositories.base import BaseRepository

if TYPE_CHECKING:
    from app.schemas.analytics import AnalyticsDailyIn

_METRICS = ("impressions", "clicks", "conversions", "leads", "spend", "revenue")
_CONFLICT_COLUMNS = ("client_id", "date", "platform")


class AnalyticsRepository(BaseRepository[AnalyticsDaily]):
    model = AnalyticsDaily

    def bulk_upsert(
        self, client_id: uuid.UUID, rows: list[AnalyticsDailyIn], *, source: str
    ) -> int:
        """Upsert ``rows`` on ``(client_id, date, platform)`` in one statement.

        Within a batch, a later row for the same (date, platform) wins — same
        last-write-wins semantics as the previous row-by-row loop — since a
        single ``ON CONFLICT`` statement can't affect the same target row
        twice. Returns the number of distinct cells upserted.
        """
        if not rows:
            return 0

        dedup: dict[tuple[date, str], dict[str, Any]] = {}
        for row in rows:
            platform_value = getattr(row.platform, "value", row.platform)
            values: dict[str, Any] = {
                "client_id": client_id,
                "date": row.date,
                "platform": row.platform,
                "source": source,
            }
            for m in _METRICS:
                values[m] = getattr(row, m)
            dedup[(row.date, platform_value)] = values

        dialect = self.db.bind.dialect.name if self.db.bind is not None else ""
        insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = insert_fn(AnalyticsDaily).values(list(dedup.values()))
        set_ = {m: getattr(stmt.excluded, m) for m in _METRICS}
        set_["source"] = stmt.excluded.source
        stmt = stmt.on_conflict_do_update(index_elements=list(_CONFLICT_COLUMNS), set_=set_)
        self.db.execute(stmt)
        return len(dedup)

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
                    func.coalesce(func.sum(AnalyticsDaily.conversions), 0).label("conversions"),
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

    def distinct_sources(
        self,
        client_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        platform: SocialPlatform | None = None,
    ) -> list[str]:
        """Provenance values present in the window, so the UI can label seeded data."""
        stmt = self._scope(
            select(AnalyticsDaily.source).distinct(),
            client_id,
            start=start,
            end=end,
            platform=platform,
        )
        return sorted(s for s in self.db.scalars(stmt).all() if s)

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
