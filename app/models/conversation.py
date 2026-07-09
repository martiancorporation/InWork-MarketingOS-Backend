"""Client conversations, messages, recipients and attachments.

Models a shared per-client inbox: ``folder`` and ``is_read`` are properties of
the shared thread/message, not per-user state.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    GUID,
    CreatedAtMixin,
    TZDateTime,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import ConversationSource, MessageFolder, RecipientKind

if TYPE_CHECKING:
    from app.models.client import Client


class Conversation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_client_last_msg", "client_id", "last_message_at"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[ConversationSource] = mapped_column(
        pg_enum(ConversationSource, "conversation_source"),
        nullable=False,
        default=ConversationSource.email,
    )
    is_read: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    client: Mapped["Client"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )  # null when inbound from an external contact
    sender_email: Mapped[str | None] = mapped_column(String(255))
    folder: Mapped[MessageFolder] = mapped_column(
        pg_enum(MessageFolder, "message_folder"),
        nullable=False,
        default=MessageFolder.inbox,
        index=True,
    )
    # Free-text label (Campaigns / Approvals / Reports / Billing today) — a plain
    # string, not an enum, since agencies customize their own mail categories.
    category: Mapped[str | None] = mapped_column(String(40), index=True)
    is_starred: Mapped[bool] = mapped_column(default=False, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    recipients: Mapped[list["MessageRecipient"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["MessageAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MessageRecipient(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "message_recipients"

    message_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[RecipientKind] = mapped_column(
        pg_enum(RecipientKind, "recipient_kind"), nullable=False, default=RecipientKind.to
    )

    message: Mapped["Message"] = relationship(back_populates="recipients")


class MessageAttachment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "message_attachments"

    message_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    message: Mapped["Message"] = relationship(back_populates="attachments")
