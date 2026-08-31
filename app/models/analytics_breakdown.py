"""Dimensional breakdowns for analytics-only integrations (GA4, Search
Console) — top pages, traffic channels, devices, search queries. Distinct from
``app/models/platform_insight.py``: that schema models an **ad platform**
(campaign -> ad set -> ad); GA4 and Search Console have no such hierarchy, only
dimensional slices of traffic. A snapshot per sync (not a daily series like
``PlatformMetricDaily``) — a "top 10 pages this period" table, refreshed in
place each sync, not accumulated day over day.

Scoped by ``integration_key`` (plain string, e.g. ``"ga4"``,
``"search_console"``), matching the Platform Insights convention.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client import Client


class AnalyticsBreakdown(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_breakdowns"
    __table_args__ = (
        UniqueConstraint("client_id", "integration_key", "breakdown_type", "dimension"),
        Index(
            "ix_analytics_breakdowns_client_key_type",
            "client_id",
            "integration_key",
            "breakdown_type",
        ),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_key: Mapped[str] = mapped_column(String(30), nullable=False)
    # e.g. "top_page", "channel", "device" (GA4); "top_query", "top_page", "device" (Search Console)
    breakdown_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # the dimension's own value — a page path, a search query, a channel/device name
    dimension: Mapped[str] = mapped_column(String(500), nullable=False)
    # stable top-N ordering within (client, integration_key, breakdown_type); 0 = highest
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # metric shape differs per breakdown_type (sessions/users for GA4 vs
    # clicks/impressions/ctr/position for Search Console) — no shared column
    # set is worth forcing, so it's a plain JSONB bag.
    metrics: Mapped[dict] = mapped_column(JSONColumn, nullable=False)

    client: Mapped[Client] = relationship(back_populates="analytics_breakdowns")
