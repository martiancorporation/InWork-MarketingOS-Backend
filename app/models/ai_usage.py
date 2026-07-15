"""AI usage events — the dedicated, immutable per-request AI billing log.

One row per AI provider call: who triggered it, which client it belongs to,
where in the app it originated, the model used, token consumption (input /
output / cache), and the dollar cost **snapshotted at call time** (so historical
reports stay stable even when prices later change).

Decoupled like the audit trail: ``actor_user_id`` / ``client_id`` use
``ON DELETE SET NULL`` so usage history survives after the user or client is
removed. ``feature`` and ``provider``/``model`` are plain indexed strings (open
sets that grow with the product), never enums.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, CreatedAtMixin, JSONColumn, UUIDPrimaryKeyMixin

_COST = Numeric(14, 6)  # USD with 6 decimals — fractions of a cent per request


class AiUsageEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ix_ai_usage_client_created", "client_id", "created_at"),
        Index("ix_ai_usage_actor_created", "actor_user_id", "created_at"),
        Index("ix_ai_usage_feature", "feature"),
        Index("ix_ai_usage_model", "model"),
        Index("ix_ai_usage_created_at", "created_at"),
    )

    # --- attribution: who / which client / where ---
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="SET NULL"), index=True
    )
    feature: Mapped[str] = mapped_column(String(80), nullable=False)  # origin, e.g. "onboarding.brand_extraction"

    # --- provider / model / call type ---
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="anthropic")
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)  # complete / complete_with_image / analyze_url

    # --- token consumption ---
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- cost (USD, snapshotted at call time) ---
    input_cost: Mapped[float] = mapped_column(_COST, nullable=False, default=0)
    output_cost: Mapped[float] = mapped_column(_COST, nullable=False, default=0)
    cache_cost: Mapped[float] = mapped_column(_COST, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(_COST, nullable=False, default=0, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    # False when the model had no pricing entry — tokens are still recorded.
    priced: Mapped[bool] = mapped_column(nullable=False, default=True)

    # --- outcome / traceability ---
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")  # success | error
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(80))  # provider response id
    meta: Mapped[dict | None] = mapped_column(JSONColumn)
