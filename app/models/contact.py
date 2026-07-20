"""Client contacts — descriptive directory of people on the client / InWork side.

Free-text info only (not access control — that's ``ClientAssignment``).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import ContactSide

if TYPE_CHECKING:
    from app.models.client import Client


class ClientContact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "client_contacts"
    __table_args__ = (Index("ix_client_contacts_client_side", "client_id", "side"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    side: Mapped[ContactSide] = mapped_column(pg_enum(ContactSide, "contact_side"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str | None] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    client: Mapped[Client] = relationship(back_populates="contacts")
