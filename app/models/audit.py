"""Immutable audit trail.

Deliberately decoupled: ``actor_user_id`` and ``client_id`` use ``ON DELETE SET
NULL`` so audit rows survive even after the user or client they reference is
removed. ``entity`` + ``entity_id`` form a generic pointer to any record.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, GUID, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import AuditAction

if TYPE_CHECKING:  # pragma: no cover
    pass


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity", "entity_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="SET NULL"), index=True
    )
    entity: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. client, event
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    action: Mapped[AuditAction] = mapped_column(
        pg_enum(AuditAction, "audit_action"), nullable=False
    )
    meta: Mapped[dict | None] = mapped_column(JSONB)
