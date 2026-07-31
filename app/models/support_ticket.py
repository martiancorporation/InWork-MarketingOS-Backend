"""Support & feedback ticket system.

A global, owner-scoped resource — unlike most of the app it is **not** tied to
a ``client_id``. Any authenticated user can file a ticket about the product
itself; they see only their own tickets, an admin sees (and can act on) every
ticket. Structurally it mirrors ``app/models/upload.py`` (global, owned by
``created_by``, admin-sees-all) rather than the client-scoped features.

Attachments reference the global ``uploads`` table directly (upload first via
``POST /uploads``, then pass the id when creating/updating a ticket) — the app
already has one reusable upload system; tickets don't need a second one.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import TicketCategory, TicketPriority, TicketStatus

if TYPE_CHECKING:
    from app.models.upload import Upload


class SupportTicket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        Index("ix_support_tickets_owner_status", "created_by", "status"),
        Index("ix_support_tickets_status_priority", "status", "priority"),
    )

    # Short, human-friendly id shown to the reporter/support team (e.g.
    # "TCK-A1B2C3D4") — the UUID primary key is still the real identifier used
    # by every route/FK; this is purely a display/search convenience.
    ticket_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)

    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[TicketCategory] = mapped_column(
        pg_enum(TicketCategory, "ticket_category"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[TicketPriority] = mapped_column(
        pg_enum(TicketPriority, "ticket_priority"),
        nullable=False,
        default=TicketPriority.medium,
        index=True,
    )
    status: Mapped[TicketStatus] = mapped_column(
        pg_enum(TicketStatus, "ticket_status"),
        nullable=False,
        default=TicketStatus.open,
        index=True,
    )

    # SET NULL (not CASCADE): a ticket is a record of a real support
    # interaction and should survive the reporter's account being removed,
    # same treatment as Upload.uploaded_by.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    attachments: Mapped[list[SupportTicketAttachment]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    replies: Mapped[list[SupportTicketReply]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportTicketReply.created_at",
    )


class SupportTicketAttachment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "support_ticket_attachments"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The global upload system (app/models/upload.py) — not a per-feature copy.
    upload_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False
    )

    ticket: Mapped[SupportTicket] = relationship(back_populates="attachments")
    # One-way (Upload doesn't need to know about tickets referencing it) —
    # eager-loaded by the repository so rendering filename/download_url never
    # costs an extra query per attachment.
    upload: Mapped[Upload] = relationship(viewonly=True)


class SupportTicketReply(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One reply/comment in a ticket's back-and-forth thread — either the
    reporter following up or a support agent (admin) responding."""

    __tablename__ = "support_ticket_replies"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    # Snapshot of the author's role at post time ("user" | "admin") — survives
    # the author's account being deleted or their role changing later, so the
    # thread always renders correctly (e.g. "Support Team" vs "Reporter").
    author_role: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    ticket: Mapped[SupportTicket] = relationship(back_populates="replies")
