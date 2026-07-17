"""Marketing campaigns — the project-level rollup above individual calendar events.

A campaign groups posts/ads (``marketing_events.campaign_id``) and carries both
the KPI *targets* the client agreed to and the *actual* rollup metrics (fed
later by the ad-platform integrations, editable now). Comparing two campaigns'
actuals is the backend for the web A/B view; comparing actuals to targets is the
project-level campaign-health score — deliberately a cross-platform,
goal-relative score, not a single-platform one.

Enum-ish fields are stored as plain indexed strings (the ai_usage/knowledge
precedent) so they stay portable and can grow without a migration.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AdObjective, CampaignStatus

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.client import Client
    from app.models.event import MarketingEvent


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_client_status", "client_id", "status"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AdObjective.awareness.value
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CampaignStatus.draft.value, index=True
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    budget_usd: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)

    # ---- KPI targets (what the client expects) ----
    target_cpl: Mapped[float | None] = mapped_column(Numeric(12, 2))
    target_ctr: Mapped[float | None] = mapped_column(Numeric(6, 3))  # percent, e.g. 2.5
    target_conversion_rate: Mapped[float | None] = mapped_column(Numeric(6, 3))  # percent

    # ---- Actual rollup metrics (fed by integrations / manual ingest) ----
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spend: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="campaigns")
    events: Mapped[list[MarketingEvent]] = relationship(back_populates="campaign")
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
