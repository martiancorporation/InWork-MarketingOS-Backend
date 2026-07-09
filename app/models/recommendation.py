"""Persisted decisions on AI recommendations (accept / modify / reject).

AI recommendations themselves are generated on demand by the ``ai`` layer; this
table records the human decision on each one so the dashboard can show what is
still pending and keep an accountable trail of who decided what.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import RecommendationDecision

if TYPE_CHECKING:
    from app.models.client import Client


class RecommendationAction(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "recommendation_actions"
    __table_args__ = (Index("ix_recommendation_actions_client_rec", "client_id", "rec_key"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rec_key: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. "rec-budget-shift"
    decision: Mapped[RecommendationDecision] = mapped_column(
        pg_enum(RecommendationDecision, "recommendation_decision"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped["Client"] = relationship(back_populates="recommendation_actions")
