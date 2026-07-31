"""Cached AI dashboard bundle — one row per client, upserted in place.

Unlike ``ClientProfile`` (append-only, versioned — other tables reference old
versions), nothing else ever needs an "old" dashboard snapshot: this is a pure
cache-replace, keyed by a hash of the inputs that produced it
(``DashboardService._hash_inputs``), so a request with unchanged inputs can
return the stored payload instead of re-running 4-6 AI calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, JSONColumn, TZDateTime, UUIDPrimaryKeyMixin


class DashboardSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "dashboard_snapshots"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # The full serialized DashboardResponse (health_score, executive_brief,
    # watchdog, recommendations, qa_review, ai_generated).
    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
