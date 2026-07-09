"""Client core model + brand sub-tables (colors, fonts, platforms).

The ``Client`` is the central entity; almost everything else hangs off it and is
removed with it via ``ON DELETE CASCADE``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    GUID,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import ClientStatus, SocialPlatform

if TYPE_CHECKING:
    from app.models.ai import AiChat, AiSource
    from app.models.analytics import AnalyticsDaily, StrategyVisual
    from app.models.compliance import ComplianceDoc, ComplianceEntry
    from app.models.contact import ClientContact, ClientMember
    from app.models.conversation import Conversation
    from app.models.document import Document
    from app.models.event import MarketingEvent
    from app.models.integration import Integration
    from app.models.organization import Organization
    from app.models.plan import PlanTask
    from app.models.recommendation import RecommendationAction
    from app.models.report import Report


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("organization_id", "slug"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    business_type: Mapped[str | None] = mapped_column(String(120))
    industry: Mapped[str | None] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(160))
    timezone: Mapped[str | None] = mapped_column(String(60))  # IANA tz for scheduling
    markets: Mapped[str | None] = mapped_column(Text)
    about_brand: Mapped[str | None] = mapped_column(Text)
    brand_voice: Mapped[str | None] = mapped_column(Text)
    brand_extracted: Mapped[str | None] = mapped_column(Text)
    color_guidelines: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    goals: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ClientStatus] = mapped_column(
        pg_enum(ClientStatus, "client_status"),
        nullable=False,
        default=ClientStatus.onboarding,
        index=True,
    )
    # Denormalized caches — refreshed together by a job/trigger, never edited by hand.
    spend_total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    leads_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpl: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    organization: Mapped["Organization"] = relationship(back_populates="clients")
    brand_colors: Mapped[list["ClientBrandColor"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    brand_fonts: Mapped[list["ClientBrandFont"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    platforms: Mapped[list["ClientPlatform"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["ClientContact"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    members: Mapped[list["ClientMember"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    compliance_entries: Mapped[list["ComplianceEntry"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    compliance_docs: Mapped[list["ComplianceDoc"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    integrations: Mapped[list["Integration"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    events: Mapped[list["MarketingEvent"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["PlanTask"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    analytics: Mapped[list["AnalyticsDaily"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    strategy_visuals: Mapped[list["StrategyVisual"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    ai_chats: Mapped[list["AiChat"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    ai_sources: Mapped[list["AiSource"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    reports: Mapped[list["Report"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    recommendation_actions: Mapped[list["RecommendationAction"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class ClientBrandColor(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "client_brand_colors"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hex: Mapped[str] = mapped_column(String(9), nullable=False)  # #RRGGBB or #RRGGBBAA
    label: Mapped[str | None] = mapped_column(String(60))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    client: Mapped["Client"] = relationship(back_populates="brand_colors")


class ClientBrandFont(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "client_brand_fonts"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family: Mapped[str] = mapped_column(String(120), nullable=False)
    usage: Mapped[str | None] = mapped_column(String(60))  # heading / body / accent

    client: Mapped["Client"] = relationship(back_populates="brand_fonts")


class ClientPlatform(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "client_platforms"
    __table_args__ = (UniqueConstraint("client_id", "platform"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        pg_enum(SocialPlatform, "social_platform"), nullable=False
    )

    client: Mapped["Client"] = relationship(back_populates="platforms")
