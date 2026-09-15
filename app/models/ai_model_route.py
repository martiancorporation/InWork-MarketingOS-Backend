"""Admin-editable AI model routing table — which model handles which class of
AI work, updatable without a redeploy.

One row per ``task_category`` (see ``app.ai.model_router.AiTaskCategory``).
``app.ai.model_router.model_for`` reads active rows (through a short-lived
in-process cache) and falls back to a built-in default when a category has no
row yet or the table can't be reached — the DB is an override layer, never a
hard requirement, same graceful-degradation stance as every other AI feature.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    pass


class AiModelRoute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_model_routes"
    __table_args__ = (UniqueConstraint("task_category"),)

    # Plain string (open set, like AiFeature) — new categories never need a
    # migration, matching the "feature"/"provider" columns on AiUsageEvent.
    task_category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(80), nullable=False)
    # Informational only today (no automatic retry-on-failure wiring) — a
    # documented next choice for whoever tunes this category, not a live path.
    fallback_model_id: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
