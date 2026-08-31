"""Sync + read Analytics Breakdowns (GA4/Search Console dimensional slices —
top pages, channels, devices, search queries). See
``app/models/analytics_breakdown.py`` for why this is a separate concept from
Platform Insights (no campaign/ad hierarchy here — just ranked dimensions).

Repository flushes only; this service owns the commit (same discipline as
every other service).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams
from app.integrations.google.ga4 import Ga4Client
from app.integrations.google.search_console import SearchConsoleClient
from app.repositories.analytics_breakdown_repository import AnalyticsBreakdownRepository
from app.schemas.analytics_breakdown import AnalyticsBreakdownListResponse, AnalyticsBreakdownRead

_GA4_KEY = "ga4"
_SEARCH_CONSOLE_KEY = "search_console"


class AnalyticsBreakdownService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = AnalyticsBreakdownRepository(db)

    async def sync_ga4(
        self, client_id: uuid.UUID, ga4_client: Ga4Client, access_token: str, property_id: str
    ) -> int:
        raw = await ga4_client.fetch_breakdowns(access_token, property_id)
        count = self.repo.replace_breakdowns(client_id, _GA4_KEY, _flatten(raw))
        self.db.commit()
        return count

    async def sync_search_console(
        self,
        client_id: uuid.UUID,
        search_console_client: SearchConsoleClient,
        access_token: str,
        site_url: str,
    ) -> int:
        raw = await search_console_client.fetch_breakdowns(access_token, site_url)
        count = self.repo.replace_breakdowns(client_id, _SEARCH_CONSOLE_KEY, _flatten(raw))
        self.db.commit()
        return count

    def list_breakdowns(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        pagination: PaginationParams,
        breakdown_type: str | None = None,
    ) -> AnalyticsBreakdownListResponse:
        rows, total = self.repo.list_breakdowns(
            client_id,
            integration_key,
            breakdown_type=breakdown_type,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return AnalyticsBreakdownListResponse(
            items=[AnalyticsBreakdownRead.model_validate(r) for r in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )


def _flatten(raw: dict[str, list[dict]]) -> list[dict]:
    """``{"top_page": [{"dimension", "metrics"}, ...], ...}`` -> flat upsert
    rows, rank assigned by each breakdown type's own response order."""
    rows = []
    for breakdown_type, items in raw.items():
        for rank, item in enumerate(items):
            rows.append(
                {
                    "breakdown_type": breakdown_type,
                    "dimension": item["dimension"],
                    "rank": rank,
                    "metrics": item["metrics"],
                }
            )
    return rows
