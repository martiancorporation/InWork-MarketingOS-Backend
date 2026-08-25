"""Provider-agnostic ad-platform insight data: campaigns → ad sets → ads,
daily performance metrics, platform-native recommendations, and derived
delivery issues.

Scoped by ``integration_key`` (a plain string, e.g. ``"meta"``,
``"google_ads"``) rather than a native Postgres enum, so a future platform
(LSA, GA4, …) never requires a migration to add a new value here — only
``app/models/enums.IntegrationKey`` (used by ``Integration`` itself) is an
enum.

Every row carries a JSONB ``raw_payload``/``detail`` catch-all with the full
provider response alongside promoted structured columns, so new fields the
UI doesn't use yet don't require a migration either.

Naming: "recommendation"/"issue" here are Meta's/the platform's own native
signals — distinct from the AI-generated Watchdog/Recommendations
(``app/ai/recommendations.py``) and the KPI-threshold ``Alert`` model.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Date as SADate
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, JSONColumn, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client import Client


class PlatformCampaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_campaigns"
    __table_args__ = (
        UniqueConstraint("client_id", "integration_key", "external_id"),
        Index("ix_platform_campaigns_client_key", "client_id", "integration_key"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_key: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(30))
    effective_status: Mapped[str | None] = mapped_column(String(30))
    daily_budget: Mapped[float | None] = mapped_column(Numeric(14, 2))
    lifetime_budget: Mapped[float | None] = mapped_column(Numeric(14, 2))
    start_time: Mapped[datetime | None] = mapped_column(TZDateTime)
    stop_time: Mapped[datetime | None] = mapped_column(TZDateTime)
    raw_payload: Mapped[dict | None] = mapped_column(JSONColumn)

    client: Mapped[Client] = relationship(back_populates="platform_campaigns")
    ad_sets: Mapped[list[PlatformAdSet]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class PlatformAdSet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_ad_sets"
    __table_args__ = (
        UniqueConstraint("client_id", "integration_key", "external_id"),
        Index("ix_platform_ad_sets_campaign", "campaign_id"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("platform_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    integration_key: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str | None] = mapped_column(String(30))
    effective_status: Mapped[str | None] = mapped_column(String(30))
    daily_budget: Mapped[float | None] = mapped_column(Numeric(14, 2))
    lifetime_budget: Mapped[float | None] = mapped_column(Numeric(14, 2))
    targeting_summary: Mapped[dict | None] = mapped_column(JSONColumn)
    raw_payload: Mapped[dict | None] = mapped_column(JSONColumn)

    campaign: Mapped[PlatformCampaign] = relationship(back_populates="ad_sets")
    ads: Mapped[list[PlatformAd]] = relationship(
        back_populates="ad_set", cascade="all, delete-orphan"
    )


class PlatformAd(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_ads"
    __table_args__ = (
        UniqueConstraint("client_id", "integration_key", "external_id"),
        Index("ix_platform_ads_ad_set", "ad_set_id"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ad_set_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("platform_ad_sets.id", ondelete="CASCADE"), nullable=False
    )
    integration_key: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str | None] = mapped_column(String(30))
    effective_status: Mapped[str | None] = mapped_column(String(30))
    creative_summary: Mapped[dict | None] = mapped_column(JSONColumn)
    issues_info: Mapped[dict | None] = mapped_column(JSONColumn)
    ad_review_feedback: Mapped[dict | None] = mapped_column(JSONColumn)
    raw_payload: Mapped[dict | None] = mapped_column(JSONColumn)

    ad_set: Mapped[PlatformAdSet] = relationship(back_populates="ads")


class PlatformMetricDaily(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Day-by-day performance for one entity (account/campaign/ad set/ad).

    ``entity_type="account"`` uses the connected account id as ``entity_id``
    (sentinel, not NULL) so the unique constraint stays simple and portable
    across Postgres/SQLite.
    """

    __tablename__ = "platform_metrics_daily"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "integration_key", "entity_type", "entity_id", "date"
        ),
        Index("ix_platform_metrics_daily_client_date", "client_id", "date"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_key: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(60), nullable=False)
    date: Mapped[date] = mapped_column(SADate, nullable=False)
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    spend: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    reach: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    frequency: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0)
    cpm: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    cpc: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    actions: Mapped[dict | None] = mapped_column(JSONColumn)
    cost_per_action_type: Mapped[dict | None] = mapped_column(JSONColumn)
    breakdowns: Mapped[dict | None] = mapped_column(JSONColumn)

    client: Mapped[Client] = relationship(back_populates="platform_metrics_daily")


class PlatformRecommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A platform-native recommendation (e.g. Meta's ``/recommendations``).

    Distinct from the AI-generated recommendations surfaced elsewhere in the
    product — this is the platform's own suggestion, verbatim.
    """

    __tablename__ = "platform_recommendations"
    __table_args__ = (
        UniqueConstraint("client_id", "integration_key", "rec_key"),
        Index("ix_platform_recommendations_client_status", "client_id", "status"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_key: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(60), nullable=False)
    code: Mapped[str | None] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    rec_key: Mapped[str] = mapped_column(String(160), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSONColumn)

    client: Mapped[Client] = relationship(back_populates="platform_recommendations")


class PlatformDeliveryIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A derived delivery problem (status/effective_status divergence, ad
    disapproval, …) — computed by our sync, not fetched verbatim."""

    __tablename__ = "platform_delivery_issues"
    __table_args__ = (
        UniqueConstraint("client_id", "integration_key", "rec_key"),
        Index("ix_platform_delivery_issues_client_status", "client_id", "status"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_key: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONColumn)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    rec_key: Mapped[str] = mapped_column(String(160), nullable=False)

    client: Mapped[Client] = relationship(back_populates="platform_delivery_issues")
