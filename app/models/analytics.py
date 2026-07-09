"""Analytics facts and AI-generated strategy visuals.

``analytics_daily`` is a daily fact table (one row per client/date/platform) —
the aggregation source for dashboards and reports.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import SocialPlatform

if TYPE_CHECKING:
    from app.models.client import Client


class AnalyticsDaily(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "analytics_daily"
    __table_args__ = (
        UniqueConstraint("client_id", "date", "platform"),
        Index("ix_analytics_daily_client_date", "client_id", "date"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[SocialPlatform] = mapped_column(
        pg_enum(SocialPlatform, "social_platform"), nullable=False
    )
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spend: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    client: Mapped["Client"] = relationship(back_populates="analytics")


class StrategyVisual(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "strategy_visuals"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped["Client"] = relationship(back_populates="strategy_visuals")
