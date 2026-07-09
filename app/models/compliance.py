"""Compliance register: brand-voice / banned / required / rule / note entries,
plus links to supporting documents.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import ComplianceKind

if TYPE_CHECKING:
    from app.models.client import Client


class ComplianceEntry(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "compliance_entries"
    __table_args__ = (Index("ix_compliance_entries_client_kind", "client_id", "kind"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ComplianceKind] = mapped_column(
        pg_enum(ComplianceKind, "compliance_kind"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    client: Mapped["Client"] = relationship(back_populates="compliance_entries")


class ComplianceDoc(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "compliance_docs"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    client: Mapped["Client"] = relationship(back_populates="compliance_docs")
