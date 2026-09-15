"""Per-client monthly ad-spend budgets — combined and per-platform.

A row with ``platform="combined"`` (see ``COMBINED_PLATFORM`` in
``app/schemas/budget.py``) is the client's overall budget for the period; a row
with a named platform (e.g. ``"meta"``, ``"google_ads"``) is that platform's own
slice. Both may exist for the same period — the client's "combined budget +
per-platform breakdown" requirement. ``platform`` is a plain string (the
``platform_insight.py``/``ClientPlatform.channel`` precedent), not a native enum,
so a future ad platform never needs a migration here. A sentinel string (rather
than a nullable column) backs the "combined" row so the uniqueness constraint
below actually holds — Postgres treats multiple NULLs in a unique constraint as
distinct, which a nullable ``platform`` would silently defeat.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client import Client


class ClientBudget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_budgets"
    __table_args__ = (UniqueConstraint("client_id", "period", "platform"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "YYYY-MM" — a budget is a whole-month figure, not tied to any single day,
    # so this is validated at the schema edge rather than stored as a Date.
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_usd: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    set_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="budgets")
