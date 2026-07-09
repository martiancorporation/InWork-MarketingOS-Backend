"""Marketing calendar events and their type-specific detail tables.

``marketing_events`` is the base row (calendar entry + approval workflow).
Post/ad specifics live in 1:1 satellite tables so the base stays lean.
"""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    GUID,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import (
    AdObjective,
    ApprovalStatus,
    EventStage,
    EventType,
    SocialPlatform,
)

if TYPE_CHECKING:
    from app.models.client import Client


class MarketingEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_events"
    __table_args__ = (Index("ix_marketing_events_client_date", "client_id", "event_date"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[EventType] = mapped_column(pg_enum(EventType, "event_type"), nullable=False)
    platform: Mapped[SocialPlatform] = mapped_column(
        pg_enum(SocialPlatform, "social_platform"), nullable=False, index=True
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_time: Mapped[time] = mapped_column(Time, nullable=False)  # local to client tz
    description: Mapped[str | None] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(Text)
    # Production lifecycle (draft/scheduled/published), independent of client approval.
    stage: Mapped[EventStage] = mapped_column(
        pg_enum(EventStage, "event_stage"),
        nullable=False,
        default=EventStage.draft,
        index=True,
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        pg_enum(ApprovalStatus, "approval_status"),
        nullable=False,
        default=ApprovalStatus.pending,
        index=True,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    approval_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped["Client"] = relationship(back_populates="events")
    post: Mapped["EventPost | None"] = relationship(
        back_populates="event", cascade="all, delete-orphan", uselist=False
    )
    ad: Mapped["EventAd | None"] = relationship(
        back_populates="event", cascade="all, delete-orphan", uselist=False
    )
    assets: Mapped[list["EventAsset"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    activity: Mapped[list["EventActivity"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventPost(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "event_posts"

    event_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("marketing_events.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    image_url: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[str | None] = mapped_column(Text)  # space-separated tags

    event: Mapped["MarketingEvent"] = relationship(back_populates="post")


class EventAd(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "event_ads"

    event_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("marketing_events.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    budget_usd: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    objective: Mapped[AdObjective] = mapped_column(
        pg_enum(AdObjective, "ad_objective"), nullable=False, default=AdObjective.awareness
    )
    audience: Mapped[str | None] = mapped_column(Text)
    bid_strategy: Mapped[str | None] = mapped_column(String(60))  # e.g. "Lowest cost"
    duration_days: Mapped[int | None] = mapped_column(Integer)  # planned run window

    event: Mapped["MarketingEvent"] = relationship(back_populates="ad")


class EventAsset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "event_assets"

    event_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("marketing_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event: Mapped["MarketingEvent"] = relationship(back_populates="assets")


class EventActivity(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "event_activity"

    event_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("marketing_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)  # status_change / comment / edit
    note: Mapped[str | None] = mapped_column(Text)

    event: Mapped["MarketingEvent"] = relationship(back_populates="activity")
