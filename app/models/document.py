"""Uploaded documents & brand assets for a client.

The binary lives in object storage; this table holds metadata + the storage URL.
Other tables (compliance_docs, event_assets, message_attachments) reference these.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import DocumentKind

if TYPE_CHECKING:
    from app.models.client import Client


class Document(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_client_kind", "client_id", "kind"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[DocumentKind] = mapped_column(
        pg_enum(DocumentKind, "document_kind"), nullable=False, default=DocumentKind.other
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="documents")
