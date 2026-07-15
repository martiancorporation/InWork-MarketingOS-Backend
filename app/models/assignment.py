"""Client assignment — the access-control link between a client and a user.

A non-admin user can see a client only if a row here links them. Admins are not
listed here; they implicitly access every client (enforced in the service layer).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.user import User


class ClientAssignment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "client_assignments"
    __table_args__ = (UniqueConstraint("client_id", "user_id"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which admin made the assignment (kept for audit; survives their deletion).
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    client: Mapped[Client] = relationship(back_populates="assignments")
    user: Mapped[User] = relationship(
        back_populates="client_assignments", foreign_keys=[user_id]
    )
