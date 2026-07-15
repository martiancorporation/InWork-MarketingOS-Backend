"""User & authentication-session models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    TimestampMixin,
    TZDateTime,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.assignment import ClientAssignment


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"),
        nullable=False,
        default=UserRole.user,
        index=True,
    )
    avatar_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # Clients assigned to this user (non-admins access only these).
    client_assignments: Mapped[list[ClientAssignment]] = relationship(
        back_populates="user",
        foreign_keys="ClientAssignment.user_id",
        cascade="all, delete-orphan",
    )


class UserSession(UUIDPrimaryKeyMixin, Base):
    """Server-side session for token revocation. Table name stays ``sessions``."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")
