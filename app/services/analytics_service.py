"""Analytics use-cases: ingest daily facts and aggregate them for dashboards.

Ingest upserts on the natural key (client, date, platform) so a re-sync overwrites
a day's numbers rather than duplicating them. Client-access scoping is enforced
at the router; the repository additionally filters every query by client_id.
Repositories flush; this service owns the commit.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.core.pagination import PaginationParams
from app.models.analytics import AnalyticsDaily
from app.models.enums import SocialPlatform
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas.analytics import (
    AnalyticsCsvImportResponse,
    AnalyticsDailyIn,
    AnalyticsDailyListResponse,
    AnalyticsDailyRead,
    AnalyticsSummary,
    AnalyticsTotals,
    DailySeriesRow,
    PlatformBreakdownRow,
)

_METRICS = ("impressions", "clicks", "conversions", "leads", "spend", "revenue")
_CSV_MAX_ROWS = 5000


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.analytics = AnalyticsRepository(db)

    def ingest(self, client_id: uuid.UUID, rows: list[AnalyticsDailyIn]) -> int:
        for row in rows:
            existing = self.analytics.get_cell(client_id, row.date, row.platform)
            if existing is None:
                self.analytics.add(
                    AnalyticsDaily(
                        client_id=client_id,
                        date=row.date,
                        platform=row.platform,
                        impressions=row.impressions,
                        clicks=row.clicks,
                        conversions=row.conversions,
                        leads=row.leads,
                        spend=row.spend,
                        revenue=row.revenue,
                    )
                )
            else:
                for m in _METRICS:
                    setattr(existing, m, getattr(row, m))
        self.db.commit()
        return len(rows)

    def import_csv(self, client_id: uuid.UUID, raw: bytes) -> AnalyticsCsvImportResponse:
        """Parse an uploaded CSV of daily facts and upsert it.

        Columns: ``date, platform`` (required) + any of the metric columns
        (``impressions, clicks, conversions, leads, spend, revenue``). Malformed
        rows are skipped and reported so a partial upload still lands. Lets
        operators push reporting data via CSV until live integrations land.
        """
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BadRequestError("File is not valid UTF-8 text.") from exc
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "date" not in reader.fieldnames:
            raise BadRequestError("CSV must have a header row with at least 'date' and 'platform'.")

        rows: list[AnalyticsDailyIn] = []
        errors: list[str] = []
        for i, record in enumerate(reader, start=2):  # row 1 is the header
            if len(rows) >= _CSV_MAX_ROWS:
                errors.append(f"Stopped at {_CSV_MAX_ROWS} rows; the rest were ignored.")
                break
            payload = {k: (v.strip() if isinstance(v, str) else v) for k, v in record.items() if k}
            payload = {k: v for k, v in payload.items() if v not in (None, "")}
            try:
                rows.append(AnalyticsDailyIn(**payload))
            except ValidationError as exc:
                first = exc.errors()[0]
                errors.append(f"Row {i}: {first.get('loc', ['?'])[0]} — {first.get('msg')}")
        upserted = self.ingest(client_id, rows) if rows else 0
        return AnalyticsCsvImportResponse(
            upserted=upserted, skipped=len(errors), errors=errors[:50]
        )

    def list_daily(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        start: date | None = None,
        end: date | None = None,
        platform: SocialPlatform | None = None,
    ) -> AnalyticsDailyListResponse:
        rows, total = self.analytics.list_daily(
            client_id,
            start=start,
            end=end,
            platform=platform,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        items = [AnalyticsDailyRead.model_validate(r) for r in rows]
        return AnalyticsDailyListResponse(
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def summary(
        self,
        client_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        platform: SocialPlatform | None = None,
    ) -> AnalyticsSummary:
        raw = self.analytics.totals(client_id, start=start, end=end, platform=platform)
        totals = self._totals(raw)
        by_platform = [
            PlatformBreakdownRow(
                platform=r["platform"],
                impressions=int(r["impressions"]),
                clicks=int(r["clicks"]),
                leads=int(r["leads"]),
                spend=float(r["spend"]),
                revenue=float(r["revenue"]),
            )
            for r in self.analytics.by_platform(client_id, start=start, end=end)
        ]
        daily = [
            DailySeriesRow(
                date=r["date"],
                impressions=int(r["impressions"]),
                clicks=int(r["clicks"]),
                leads=int(r["leads"]),
                spend=float(r["spend"]),
                revenue=float(r["revenue"]),
            )
            for r in self.analytics.daily_series(client_id, start=start, end=end, platform=platform)
        ]
        return AnalyticsSummary(totals=totals, by_platform=by_platform, daily=daily)

    @staticmethod
    def _totals(raw: dict) -> AnalyticsTotals:
        impressions = int(raw["impressions"])
        clicks = int(raw["clicks"])
        leads = int(raw["leads"])
        spend = float(raw["spend"])
        revenue = float(raw["revenue"])
        return AnalyticsTotals(
            impressions=impressions,
            clicks=clicks,
            conversions=int(raw["conversions"]),
            leads=leads,
            spend=spend,
            revenue=revenue,
            ctr=round(clicks / impressions * 100, 2) if impressions else 0.0,
            cpl=round(spend / leads, 2) if leads else 0.0,
            roas=round(revenue / spend, 2) if spend else 0.0,
        )
