"""AI assistant chats, messages, and the sources grounding each chat.

``ai_sources.ref_id`` is a deliberate polymorphic pointer (target depends on
``type``) so it carries no DB-level foreign key — resolve it in the service layer.
``ai_chat_sources`` is the chat↔source association table.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    GUID,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import AiRole, AiSourceType

if TYPE_CHECKING:
    from app.models.client import Client


class AiChat(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_chats"
    __table_args__ = (Index("ix_ai_chats_client_user", "client_id", "user_id"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(200))

    client: Mapped["Client"] = relationship(back_populates="ai_chats")
    messages: Mapped[list["AiChatMessage"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    source_links: Mapped[list["AiChatSource"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )


class AiChatMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ai_chat_messages"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("ai_chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[AiRole] = mapped_column(pg_enum(AiRole, "ai_chat_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int | None] = mapped_column(Integer)

    chat: Mapped["AiChat"] = relationship(back_populates="messages")


class AiSource(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ai_sources"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[AiSourceType] = mapped_column(
        pg_enum(AiSourceType, "ai_source_type"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(GUID)  # polymorphic; no FK by design

    client: Mapped["Client"] = relationship(back_populates="ai_sources")
    chat_links: Mapped[list["AiChatSource"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class AiChatSource(Base):
    __tablename__ = "ai_chat_sources"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("ai_chats.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("ai_sources.id", ondelete="CASCADE"), primary_key=True
    )

    chat: Mapped["AiChat"] = relationship(back_populates="source_links")
    source: Mapped["AiSource"] = relationship(back_populates="chat_links")
