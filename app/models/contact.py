"""Client people: descriptive contacts + internal access members.

- ``ClientContact`` — free-text people on the client or InWork side (directory info).
- ``ClientMember`` — links an internal ``User`` to a client for access control
  ("who can work on this client, and in what role").
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    GUID,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import ContactSide, UserRole

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.user import User


class ClientContact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "client_contacts"
    __table_args__ = (Index("ix_client_contacts_client_side", "client_id", "side"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    side: Mapped[ContactSide] = mapped_column(
        pg_enum(ContactSide, "contact_side"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str | None] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    client: Mapped["Client"] = relationship(back_populates="contacts")


class ClientMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_members"
    __table_args__ = (UniqueConstraint("client_id", "user_id"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), nullable=False, default=UserRole.strategist
    )

    client: Mapped["Client"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="client_memberships")
