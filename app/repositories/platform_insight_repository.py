"""Data access for the provider-agnostic Platform Insights tables
(campaigns/ad sets/ads, daily metrics, recommendations, delivery issues).

Every query is hard-filtered by ``client_id`` (tenant isolation, same stance as
every other repository). Bulk writes use one dialect-native
``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` statement per entity type —
same pattern as ``AnalyticsRepository.bulk_upsert`` — so a sync of N rows costs
one round trip, not up to ``2N``. ``RETURNING`` also hands back each row's
internal id, which the campaign→ad set→ad chain needs to resolve foreign keys
across the three inserts of a single sync.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from app.models.platform_insight import (
    PlatformAd,
    PlatformAdSet,
    PlatformCampaign,
    PlatformDeliveryIssue,
    PlatformMetricDaily,
    PlatformRecommendation,
)
from app.repositories.base import BaseRepository


class PlatformInsightRepository(BaseRepository[PlatformCampaign]):
    model = PlatformCampaign

    def _insert(self):
        dialect = self.db.bind.dialect.name if self.db.bind is not None else ""
        return pg_insert if dialect == "postgresql" else sqlite_insert

    # ---- upserts (sync path) ------------------------------------------- #

    def upsert_campaigns(
        self, client_id: uuid.UUID, integration_key: str, rows: list[dict[str, Any]]
    ) -> dict[str, uuid.UUID]:
        """Upsert on (client_id, integration_key, external_id); returns
        ``{external_id: internal_id}`` for ad sets to link against."""
        if not rows:
            return {}
        columns = (
            "name",
            "objective",
            "status",
            "effective_status",
            "daily_budget",
            "lifetime_budget",
            "start_time",
            "stop_time",
            "raw_payload",
        )
        values = [
            {
                "id": uuid.uuid4(),
                "client_id": client_id,
                "integration_key": integration_key,
                "external_id": row["external_id"],
                **{c: row.get(c) for c in columns},
            }
            for row in rows
        ]
        stmt = self._insert()(PlatformCampaign).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["client_id", "integration_key", "external_id"],
            set_={c: getattr(stmt.excluded, c) for c in columns},
        ).returning(PlatformCampaign.external_id, PlatformCampaign.id)
        return dict(self.db.execute(stmt).all())

    def upsert_ad_sets(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        rows: list[dict[str, Any]],
        campaign_ids: dict[str, uuid.UUID],
    ) -> dict[str, uuid.UUID]:
        """``rows`` carry the provider's own ``campaign_id`` (external) —
        resolved to our internal FK via ``campaign_ids``. A row whose parent
        campaign wasn't synced (shouldn't happen within one sync pass, but
        provider data can be inconsistent) is skipped rather than raising."""
        if not rows:
            return {}
        columns = (
            "name",
            "status",
            "effective_status",
            "daily_budget",
            "lifetime_budget",
            "targeting_summary",
            "raw_payload",
        )
        values = []
        for row in rows:
            campaign_id = campaign_ids.get(str(row.get("campaign_external_id")))
            if campaign_id is None:
                continue
            values.append(
                {
                    "id": uuid.uuid4(),
                    "client_id": client_id,
                    "campaign_id": campaign_id,
                    "integration_key": integration_key,
                    "external_id": row["external_id"],
                    **{c: row.get(c) for c in columns},
                }
            )
        if not values:
            return {}
        stmt = self._insert()(PlatformAdSet).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["client_id", "integration_key", "external_id"],
            set_={
                "campaign_id": stmt.excluded.campaign_id,
                **{c: getattr(stmt.excluded, c) for c in columns},
            },
        ).returning(PlatformAdSet.external_id, PlatformAdSet.id)
        return dict(self.db.execute(stmt).all())

    def upsert_ads(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        rows: list[dict[str, Any]],
        ad_set_ids: dict[str, uuid.UUID],
    ) -> int:
        if not rows:
            return 0
        columns = (
            "name",
            "status",
            "effective_status",
            "creative_summary",
            "issues_info",
            "ad_review_feedback",
            "raw_payload",
        )
        values = []
        for row in rows:
            ad_set_id = ad_set_ids.get(str(row.get("ad_set_external_id")))
            if ad_set_id is None:
                continue
            values.append(
                {
                    "id": uuid.uuid4(),
                    "client_id": client_id,
                    "ad_set_id": ad_set_id,
                    "integration_key": integration_key,
                    "external_id": row["external_id"],
                    **{c: row.get(c) for c in columns},
                }
            )
        if not values:
            return 0
        stmt = self._insert()(PlatformAd).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["client_id", "integration_key", "external_id"],
            set_={
                "ad_set_id": stmt.excluded.ad_set_id,
                **{c: getattr(stmt.excluded, c) for c in columns},
            },
        )
        self.db.execute(stmt)
        return len(values)

    def upsert_metrics_daily(
        self, client_id: uuid.UUID, integration_key: str, rows: list[dict[str, Any]]
    ) -> int:
        if not rows:
            return 0
        columns = (
            "impressions",
            "clicks",
            "spend",
            "reach",
            "frequency",
            "cpm",
            "cpc",
            "conversions",
            "revenue",
            "actions",
            "cost_per_action_type",
            "breakdowns",
        )
        dedup: dict[tuple[str, str, date], dict[str, Any]] = {}
        for row in rows:
            key = (row["entity_type"], row["entity_id"], row["date"])
            dedup[key] = {
                "id": uuid.uuid4(),
                "client_id": client_id,
                "integration_key": integration_key,
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "date": row["date"],
                **{c: row.get(c, 0) for c in columns},
            }
        stmt = self._insert()(PlatformMetricDaily).values(list(dedup.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=["client_id", "integration_key", "entity_type", "entity_id", "date"],
            set_={c: getattr(stmt.excluded, c) for c in columns},
        )
        self.db.execute(stmt)
        return len(dedup)

    def upsert_recommendations(
        self, client_id: uuid.UUID, integration_key: str, rows: list[dict[str, Any]]
    ) -> int:
        return self._upsert_rec_keyed(PlatformRecommendation, client_id, integration_key, rows)

    def upsert_delivery_issues(
        self, client_id: uuid.UUID, integration_key: str, rows: list[dict[str, Any]]
    ) -> int:
        return self._upsert_rec_keyed(PlatformDeliveryIssue, client_id, integration_key, rows)

    def _upsert_rec_keyed(
        self, model, client_id, integration_key, rows: list[dict[str, Any]]
    ) -> int:
        """Shared upsert for the two ``rec_key``-deduped tables (recommendations
        and delivery issues) — same unique key shape, different columns."""
        if not rows:
            return 0
        columns = [
            c.name
            for c in model.__table__.columns
            if c.name not in ("id", "created_at", "updated_at")
        ]
        dedup: dict[str, dict[str, Any]] = {}
        for row in rows:
            values = {
                "id": uuid.uuid4(),
                "client_id": client_id,
                "integration_key": integration_key,
            }
            for c in columns:
                if c in ("client_id", "integration_key"):
                    continue
                values[c] = row.get(c)
            dedup[row["rec_key"]] = values
        stmt = self._insert()(model).values(list(dedup.values()))
        update_cols = [c for c in columns if c not in ("client_id", "integration_key", "rec_key")]
        stmt = stmt.on_conflict_do_update(
            index_elements=["client_id", "integration_key", "rec_key"],
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
        self.db.execute(stmt)
        return len(dedup)

    # ---- reads (API path) ----------------------------------------------- #

    def list_campaigns(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[PlatformCampaign], int]:
        stmt = select(PlatformCampaign).where(
            PlatformCampaign.client_id == client_id,
            PlatformCampaign.integration_key == integration_key,
        )
        if status:
            stmt = stmt.where(PlatformCampaign.effective_status == status)
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = stmt.order_by(PlatformCampaign.name.asc()).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)

    def get_campaign(
        self, client_id: uuid.UUID, integration_key: str, campaign_id: uuid.UUID
    ) -> PlatformCampaign | None:
        return self.db.scalar(
            select(PlatformCampaign)
            .where(
                PlatformCampaign.client_id == client_id,
                PlatformCampaign.integration_key == integration_key,
                PlatformCampaign.id == campaign_id,
            )
            .options(selectinload(PlatformCampaign.ad_sets).selectinload(PlatformAdSet.ads))
        )

    def metrics_series(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        entity_type: str,
        entity_id: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[PlatformMetricDaily]:
        stmt = select(PlatformMetricDaily).where(
            PlatformMetricDaily.client_id == client_id,
            PlatformMetricDaily.integration_key == integration_key,
            PlatformMetricDaily.entity_type == entity_type,
            PlatformMetricDaily.entity_id == entity_id,
        )
        if start is not None:
            stmt = stmt.where(PlatformMetricDaily.date >= start)
        if end is not None:
            stmt = stmt.where(PlatformMetricDaily.date <= end)
        return list(self.db.scalars(stmt.order_by(PlatformMetricDaily.date.asc())).all())

    def aggregate_metrics_by_entity(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        entity_type: str,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, dict[str, float]]:
        """Summed metrics per entity over a window — one grouped query, used by
        the report generator (which needs a per-campaign total, not the daily
        series `metrics_series` returns)."""
        cols = (
            func.coalesce(func.sum(PlatformMetricDaily.impressions), 0).label("impressions"),
            func.coalesce(func.sum(PlatformMetricDaily.clicks), 0).label("clicks"),
            func.coalesce(func.sum(PlatformMetricDaily.spend), 0).label("spend"),
            func.coalesce(func.sum(PlatformMetricDaily.conversions), 0).label("conversions"),
            func.coalesce(func.sum(PlatformMetricDaily.revenue), 0).label("revenue"),
        )
        stmt = (
            select(PlatformMetricDaily.entity_id, *cols)
            .where(
                PlatformMetricDaily.client_id == client_id,
                PlatformMetricDaily.integration_key == integration_key,
                PlatformMetricDaily.entity_type == entity_type,
            )
            .group_by(PlatformMetricDaily.entity_id)
        )
        if start is not None:
            stmt = stmt.where(PlatformMetricDaily.date >= start)
        if end is not None:
            stmt = stmt.where(PlatformMetricDaily.date <= end)
        return {
            row.entity_id: {
                "impressions": float(row.impressions),
                "clicks": float(row.clicks),
                "spend": float(row.spend),
                "conversions": float(row.conversions),
                "revenue": float(row.revenue),
            }
            for row in self.db.execute(stmt).all()
        }

    def sum_spend(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        start: date,
        end: date,
    ) -> float:
        """Total spend for one platform over a date window.

        Filtered to ``entity_type="campaign"`` — the only granularity daily
        metrics are actually synced at (see ``_normalize_meta_campaign_metric``
        et al.); summing every entity type would double-count the same spend
        once campaigns gain ad-set/ad-level rows.
        """
        total = self.db.scalar(
            select(func.coalesce(func.sum(PlatformMetricDaily.spend), 0)).where(
                PlatformMetricDaily.client_id == client_id,
                PlatformMetricDaily.integration_key == integration_key,
                PlatformMetricDaily.entity_type == "campaign",
                PlatformMetricDaily.date >= start,
                PlatformMetricDaily.date <= end,
            )
        )
        return float(total or 0)

    def count_campaigns(
        self, client_id: uuid.UUID, integration_key: str
    ) -> tuple[int, int]:
        """``(total, active)`` campaign counts for one platform."""
        total = self.db.scalar(
            select(func.count()).select_from(PlatformCampaign).where(
                PlatformCampaign.client_id == client_id,
                PlatformCampaign.integration_key == integration_key,
            )
        )
        active = self.db.scalar(
            select(func.count()).select_from(PlatformCampaign).where(
                PlatformCampaign.client_id == client_id,
                PlatformCampaign.integration_key == integration_key,
                PlatformCampaign.effective_status == "ACTIVE",
            )
        )
        return int(total or 0), int(active or 0)

    def list_recommendations(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[PlatformRecommendation], int]:
        return self._list_rec_keyed(
            PlatformRecommendation,
            client_id,
            integration_key,
            status=status,
            offset=offset,
            limit=limit,
        )

    def list_delivery_issues(
        self,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[PlatformDeliveryIssue], int]:
        return self._list_rec_keyed(
            PlatformDeliveryIssue,
            client_id,
            integration_key,
            status=status,
            offset=offset,
            limit=limit,
        )

    def _list_rec_keyed(
        self,
        model,
        client_id: uuid.UUID,
        integration_key: str,
        *,
        status: str | None,
        offset: int,
        limit: int | None,
    ):
        stmt = select(model).where(
            model.client_id == client_id, model.integration_key == integration_key
        )
        if status:
            stmt = stmt.where(model.status == status)
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = stmt.order_by(model.created_at.desc()).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)

    def get_recommendation(
        self, client_id: uuid.UUID, rec_id: uuid.UUID
    ) -> PlatformRecommendation | None:
        return self.db.scalar(
            select(PlatformRecommendation).where(
                PlatformRecommendation.client_id == client_id, PlatformRecommendation.id == rec_id
            )
        )

    def get_delivery_issue(
        self, client_id: uuid.UUID, issue_id: uuid.UUID
    ) -> PlatformDeliveryIssue | None:
        return self.db.scalar(
            select(PlatformDeliveryIssue).where(
                PlatformDeliveryIssue.client_id == client_id, PlatformDeliveryIssue.id == issue_id
            )
        )
