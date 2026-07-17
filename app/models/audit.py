"""Immutable audit trail.

Deliberately decoupled: ``actor_user_id`` and ``client_id`` use ``ON DELETE SET
NULL`` so audit rows survive even after the user or client they reference is
removed. ``entity`` + ``entity_id`` form a generic structured pointer to any
record; ``target_label`` carries the human-readable description the app
actually renders (e.g. "Acme Co. (2026-06-01 → 2026-06-30)") so the UI never
needs a join just to show history.

``action`` is a plain indexed string, not an enum — the app logs free-form,
dotted action identifiers per feature (``report.pdf.exported``,
``recommendation.accepted``, ``integration.connect``, …) and that set grows
with every new feature without warranting a migration each time.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, CreatedAtMixin, JSONColumn, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity", "entity_id"),
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_action", "action"),
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="SET NULL"), index=True
    )
    entity: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. client, event
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_label: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONColumn)
    # Per-field before/after diff of the mutated record, e.g.
    # ``{"status": {"before": "active", "after": "inactive"}}`` — the
    # accountability trail ("who changed this value, and from what").
    changes: Mapped[dict | None] = mapped_column(JSONColumn)
