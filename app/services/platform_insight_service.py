"""Sync + normalize provider-native campaign/ad-set/ad hierarchies, daily
performance, recommendations, and delivery issues into the provider-agnostic
Platform Insights tables (``app/models/platform_insight.py``).

One normalizer per provider translates that provider's raw API shape into the
common row shape the repository upserts; adding a new provider later means
writing one normalizer + one client method, not touching the schema or the
sync orchestration below.

Repositories flush only; this service owns the commit (same discipline as
every other service).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, NotFoundError
from app.core.pagination import PaginationParams
from app.integrations.meta.client import MetaClient
from app.models.platform_insight import PlatformDeliveryIssue, PlatformRecommendation
from app.repositories.platform_insight_repository import PlatformInsightRepository
from app.schemas.platform_insight import (
    PlatformCampaignListResponse,
    PlatformCampaignRead,
    PlatformDeliveryIssueListResponse,
    PlatformDeliveryIssueRead,
    PlatformMetricSeriesResponse,
    PlatformRecommendationListResponse,
    PlatformRecommendationRead,
)

logger = logging.getLogger("app.services.platform_insight_service")

_META_KEY = "meta"


@dataclass
class PlatformSyncResult:
    campaigns: int
    ad_sets: int
    ads: int
    metrics: int
    recommendations: int
    delivery_issues: int


class PlatformInsightService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = PlatformInsightRepository(db)

    async def sync_meta(
        self, client_id: uuid.UUID, meta_client: MetaClient, access_token: str, ad_account_id: str
    ) -> PlatformSyncResult:
        hierarchy = await meta_client.fetch_campaign_hierarchy(access_token, ad_account_id)
        metric_rows = await meta_client.fetch_campaign_metrics_daily(access_token, ad_account_id)
        try:
            rec_rows = await meta_client.fetch_recommendations(access_token, ad_account_id)
        except AppError:
            # Not every ad account has recommendations available; this is a
            # bonus signal, not core sync data — never fail the sync for it.
            logger.info("Meta recommendations unavailable for account %s", ad_account_id)
            rec_rows = []

        raw_campaigns = hierarchy["campaigns"]
        raw_ad_sets = hierarchy["ad_sets"]
        raw_ads = hierarchy["ads"]

        campaign_ids = self.repo.upsert_campaigns(
            client_id, _META_KEY, [_normalize_meta_campaign(c) for c in raw_campaigns]
        )
        ad_set_ids = self.repo.upsert_ad_sets(
            client_id,
            _META_KEY,
            [_normalize_meta_ad_set(a) for a in raw_ad_sets],
            campaign_ids,
        )
        ads_count = self.repo.upsert_ads(
            client_id, _META_KEY, [_normalize_meta_ad(a) for a in raw_ads], ad_set_ids
        )
        metrics_count = self.repo.upsert_metrics_daily(
            client_id, _META_KEY, [_normalize_meta_campaign_metric(r) for r in metric_rows]
        )
        recs_count = self.repo.upsert_recommendations(
            client_id,
            _META_KEY,
            [_normalize_meta_recommendation(r, ad_account_id) for r in rec_rows],
        )
        issues_count = self.repo.upsert_delivery_issues(
            client_id, _META_KEY, _derive_meta_delivery_issues(raw_campaigns, raw_ad_sets, raw_ads)
        )
        self.db.commit()
        return PlatformSyncResult(
            campaigns=len(campaign_ids),
            ad_sets=len(ad_set_ids),
            ads=ads_count,
            metrics=metrics_count,
            recommendations=recs_count,
            delivery_issues=issues_count,
        )

    # ---- reads (API path) ----------------------------------------------- #

    def list_campaigns(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> PlatformCampaignListResponse:
        rows, total = self.repo.list_campaigns(
            client_id,
            integration_key,
            status=status,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return PlatformCampaignListResponse(
            items=[PlatformCampaignRead.model_validate(r) for r in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_campaign(self, client_id: uuid.UUID, integration_key: str, campaign_id: uuid.UUID):
        campaign = self.repo.get_campaign(client_id, integration_key, campaign_id)
        if campaign is None:
            raise NotFoundError("Campaign not found.")
        return campaign

    def campaign_metrics(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        campaign_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> PlatformMetricSeriesResponse:
        campaign = self.get_campaign(client_id, integration_key, campaign_id)  # 404 if inaccessible
        rows = self.repo.metrics_series(
            client_id,
            integration_key,
            entity_type="campaign",
            entity_id=campaign.external_id,
            start=start,
            end=end,
        )
        return PlatformMetricSeriesResponse(
            entity_type="campaign", entity_id=campaign.external_id, items=rows
        )

    def list_recommendations(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> PlatformRecommendationListResponse:
        rows, total = self.repo.list_recommendations(
            client_id,
            integration_key,
            status=status,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return PlatformRecommendationListResponse(
            items=[PlatformRecommendationRead.model_validate(r) for r in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def dismiss_recommendation(
        self, client_id: uuid.UUID, rec_id: uuid.UUID
    ) -> PlatformRecommendation:
        rec = self.repo.get_recommendation(client_id, rec_id)
        if rec is None:
            raise NotFoundError("Recommendation not found.")
        rec.status = "dismissed"
        self.db.commit()
        self.db.refresh(rec)
        return rec

    def list_delivery_issues(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> PlatformDeliveryIssueListResponse:
        rows, total = self.repo.list_delivery_issues(
            client_id,
            integration_key,
            status=status,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return PlatformDeliveryIssueListResponse(
            items=[PlatformDeliveryIssueRead.model_validate(r) for r in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def resolve_delivery_issue(
        self, client_id: uuid.UUID, issue_id: uuid.UUID
    ) -> PlatformDeliveryIssue:
        issue = self.repo.get_delivery_issue(client_id, issue_id)
        if issue is None:
            raise NotFoundError("Delivery issue not found.")
        issue.status = "resolved"
        self.db.commit()
        self.db.refresh(issue)
        return issue


# ---- Meta normalizers ------------------------------------------------------ #


def _money(value) -> float | None:
    """Meta budgets are minor-unit strings (cents); ``None`` means "not set"."""
    if value in (None, ""):
        return None
    try:
        return round(float(value) / 100, 2)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _normalize_meta_campaign(row: dict) -> dict:
    return {
        "external_id": row["id"],
        "name": row.get("name") or row["id"],
        "objective": row.get("objective"),
        "status": row.get("status"),
        "effective_status": row.get("effective_status"),
        "daily_budget": _money(row.get("daily_budget")),
        "lifetime_budget": _money(row.get("lifetime_budget")),
        "start_time": _parse_dt(row.get("start_time")),
        "stop_time": _parse_dt(row.get("stop_time")),
        "raw_payload": row,
    }


def _normalize_meta_ad_set(row: dict) -> dict:
    return {
        "external_id": row["id"],
        "campaign_external_id": row.get("campaign_id"),
        "name": row.get("name") or row["id"],
        "status": row.get("status"),
        "effective_status": row.get("effective_status"),
        "daily_budget": _money(row.get("daily_budget")),
        "lifetime_budget": _money(row.get("lifetime_budget")),
        "targeting_summary": row.get("targeting"),
        "raw_payload": row,
    }


def _normalize_meta_ad(row: dict) -> dict:
    return {
        "external_id": row["id"],
        "ad_set_external_id": row.get("adset_id"),
        "name": row.get("name") or row["id"],
        "status": row.get("status"),
        "effective_status": row.get("effective_status"),
        "creative_summary": row.get("creative"),
        "issues_info": row.get("issues_info"),
        "ad_review_feedback": row.get("ad_review_feedback"),
        "raw_payload": row,
    }


_LEAD_ACTIONS = {"lead", "leadgen.other", "onsite_conversion.lead_grouped"}
_CONVERSION_ACTIONS = {"purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase"}


def _sum_action_values(action_values: list | None, wanted: set[str]) -> float:
    total = 0.0
    for a in action_values or []:
        if a.get("action_type") in wanted:
            try:
                total += float(a.get("value", 0))
            except (TypeError, ValueError):
                continue
    return round(total, 2)


def _sum_actions(actions: list | None, wanted: set[str]) -> int:
    total = 0
    for a in actions or []:
        if a.get("action_type") in wanted:
            try:
                total += int(float(a.get("value", 0)))
            except (TypeError, ValueError):
                continue
    return total


def _normalize_meta_campaign_metric(row: dict) -> dict:
    date_start = row.get("date_start")
    return {
        "entity_type": "campaign",
        "entity_id": row.get("campaign_id"),
        "date": date.fromisoformat(date_start) if date_start else date.today(),
        "impressions": int(float(row.get("impressions", 0) or 0)),
        "clicks": int(float(row.get("clicks", 0) or 0)),
        "spend": round(float(row.get("spend", 0) or 0), 2),
        "reach": int(float(row.get("reach", 0) or 0)),
        "frequency": round(float(row.get("frequency", 0) or 0), 4),
        "cpm": round(float(row.get("cpm", 0) or 0), 4),
        "cpc": round(float(row.get("cpc", 0) or 0), 4),
        "conversions": _sum_actions(row.get("actions"), _CONVERSION_ACTIONS)
        + _sum_actions(row.get("actions"), _LEAD_ACTIONS),
        "revenue": _sum_action_values(row.get("action_values"), _CONVERSION_ACTIONS),
        "actions": row.get("actions"),
        "cost_per_action_type": row.get("cost_per_action_type"),
        "breakdowns": None,
    }


def _normalize_meta_recommendation(row: dict, ad_account_id: str) -> dict:
    code = str(row.get("code") or "")
    return {
        "entity_type": "account",
        "entity_id": ad_account_id,
        "code": code or None,
        "title": row.get("title") or row.get("message") or "Recommendation",
        "message": row.get("message"),
        "importance": row.get("importance"),
        "status": "open",
        "rec_key": f"account:{ad_account_id}:{code or row.get('blame_field') or row.get('message')}"[
            :160
        ],
        "raw_payload": row,
    }


def _derive_meta_delivery_issues(
    campaigns: list[dict], ad_sets: list[dict], ads: list[dict]
) -> list[dict]:
    """Not fetched — computed: status/effective_status divergence and ad
    disapproval signals, across all three levels of the hierarchy."""
    issues: list[dict] = []
    for entity_type, rows in (("campaign", campaigns), ("ad_set", ad_sets), ("ad", ads)):
        for row in rows:
            entity_id = row["id"]
            status, effective = row.get("status"), row.get("effective_status")
            if status and effective and status != effective and effective not in ("ACTIVE",):
                issues.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "severity": "medium",
                        "reason": "status_mismatch",
                        "detail": {"status": status, "effective_status": effective},
                        "status": "open",
                        "rec_key": f"{entity_type}:{entity_id}:status_mismatch",
                    }
                )
            issues_info = row.get("issues_info")
            if issues_info:
                issues.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "severity": "high",
                        "reason": "disapproved_or_flagged",
                        "detail": {"issues_info": issues_info},
                        "status": "open",
                        "rec_key": f"{entity_type}:{entity_id}:issues_info",
                    }
                )
            review_feedback = row.get("ad_review_feedback")
            if review_feedback:
                issues.append(
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "severity": "high",
                        "reason": "review_feedback",
                        "detail": {"ad_review_feedback": review_feedback},
                        "status": "open",
                        "rec_key": f"{entity_type}:{entity_id}:review_feedback",
                    }
                )
    return issues
