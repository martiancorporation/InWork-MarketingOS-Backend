"""Unified Meta/Google Ads dashboard use-case.

Combines already-synced platform data (``PlatformCampaign``/``PlatformMetricDaily``,
populated by ``IntegrationService``/``PlatformInsightService``) with the client's
budget for the period into one read — the "central paid-media monitoring hub"
the client asked for, instead of a separate call per connector. Read-only: no
commit needed.
"""

from __future__ import annotations

import calendar as _calendar
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.models.enums import IntegrationKey
from app.repositories.budget_repository import BudgetRepository
from app.repositories.integration_repository import IntegrationRepository
from app.repositories.platform_insight_repository import PlatformInsightRepository
from app.schemas.ads_overview import AdsOverviewResponse, PlatformAdsOverview
from app.schemas.budget import COMBINED_PLATFORM, validate_period

#: The ad platforms this overview covers — the client's two named integrations.
_AD_PLATFORM_KEYS = (IntegrationKey.meta, IntegrationKey.google_ads)


def _period_bounds(period: str) -> tuple[date, date]:
    year, month = (int(p) for p in period.split("-"))
    last_day = _calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


class AdsOverviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.insights = PlatformInsightRepository(db)
        self.integrations = IntegrationRepository(db)
        self.budgets = BudgetRepository(db)

    def get_overview(self, client_id: uuid.UUID, period: str) -> AdsOverviewResponse:
        try:
            validate_period(period)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

        start, end = _period_bounds(period)
        budget_by_platform = {
            row.platform: float(row.amount_usd)
            for row in self.budgets.list_for_period(client_id, period)
        }

        platforms: list[PlatformAdsOverview] = []
        combined_spend = 0.0
        for key in _AD_PLATFORM_KEYS:
            integration = self.integrations.get_for_client(client_id, key)
            spend = self.insights.sum_spend(client_id, key.value, start=start, end=end)
            total, active = self.insights.count_campaigns(client_id, key.value)
            budget = budget_by_platform.get(key.value)
            combined_spend += spend
            platforms.append(
                PlatformAdsOverview(
                    platform=key.value,
                    status=integration.status if integration else None,
                    campaign_count=total,
                    active_campaign_count=active,
                    spend_usd=spend,
                    budget_usd=budget,
                    remaining_usd=(budget - spend) if budget is not None else None,
                )
            )

        combined_budget = budget_by_platform.get(COMBINED_PLATFORM)
        return AdsOverviewResponse(
            period=period,
            combined_spend_usd=combined_spend,
            combined_budget_usd=combined_budget,
            combined_remaining_usd=(
                (combined_budget - combined_spend) if combined_budget is not None else None
            ),
            platforms=platforms,
        )
