"""Recorded strategy for a client — the AI-given plan the operator signed off on.

Each row is an immutable, versioned snapshot of the strategy text plus who signed
it. The current strategy is the highest ``version`` for the client. Adherence
(how much the operator actually followed the strategy) is computed deterministically
from recommendation decisions and plan-task completion — see ``StrategyService``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.client import Client


class Strategy(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "strategies"
    __table_args__ = (Index("ix_strategies_client_version", "client_id", "version", unique=True),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 1-based, monotonically increasing per client; the highest is "current".
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Who signed off on this strategy (kept for accountability; survives deletion).
    signed_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="strategies")
