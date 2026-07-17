"""KPI alerts — the watchdog signals surfaced to operators.

An alert is raised when a campaign's actual metric breaches its agreed KPI
target (e.g. CPL over target, CTR under target, budget pace), or as an
opportunity. It carries the offending metric + threshold + actual so the UI can
explain *why*, and an acknowledge/resolve workflow so a human owns the response
— a closed accountability loop. ``rec_key`` deduplicates
repeat evaluations of the same breach.

Enum-ish fields are plain indexed strings (portable, growable), matching the
web ``WatchdogItem`` contract (kind/severity).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AlertKind, AlertSeverity, AlertStatus

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.client import Client


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_client_status", "client_id", "status"),
        Index("ix_alerts_client_reckey", "client_id", "rec_key"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("campaigns.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AlertKind.alert.value, index=True
    )
    severity: Mapped[str] = mapped_column(
        String(8), nullable=False, default=AlertSeverity.medium.value, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AlertStatus.open.value, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    metric: Mapped[str | None] = mapped_column(String(40))  # e.g. "cpl", "ctr", "budget"
    threshold: Mapped[float | None] = mapped_column(Numeric(14, 4))
    actual: Mapped[float | None] = mapped_column(Numeric(14, 4))
    # Stable identity of the underlying breach so re-evaluation updates instead
    # of duplicating (mirrors recommendation_actions' rec_key pattern).
    rec_key: Mapped[str | None] = mapped_column(String(120))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="alerts")
    campaign: Mapped[Campaign | None] = relationship(back_populates="alerts")
