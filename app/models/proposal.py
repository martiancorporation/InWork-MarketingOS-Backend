"""The AI change-proposal engine: governed propose -> approve -> execute.

An AI chat turn that wants to mutate data never writes directly — it stages one
or more ``ProposedOperation`` rows under one ``ChangeProposal`` (validated with
a dry run against the real service layer, then rolled back; see
``ProposalService``). Nothing is applied until a human calls the approve
endpoint, which re-validates and executes every operation in one transaction.

Deliberately decoupled like ``AuditLog`` (no ORM relationships to ``Client``/
``AiChat``/``User``, just raw ``ON DELETE SET NULL`` ids) — a proposal is a
record of what was asked and what happened, and should survive the deletion of
anything it references.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, JSONColumn, TZDateTime, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import ProposalStatus, ProposedOperationStatus, ProposedOperationType


class ChangeProposal(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "change_proposals"
    __table_args__ = (Index("ix_change_proposals_client_status", "client_id", "status"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("ai_chats.id", ondelete="SET NULL"), index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("ai_chat_messages.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[ProposalStatus] = mapped_column(
        pg_enum(ProposalStatus, "proposal_status"),
        nullable=False,
        default=ProposalStatus.pending_approval,
        index=True,
    )
    # What the user asked for, verbatim — shown on the approval card and kept
    # for audit ("what prompted this"). Bounded like any other free-text field
    # (enforced in the schema layer, not here).
    raw_request: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    # Why the *whole proposal* failed (expired / lost capability / stale row) —
    # distinct from a single operation's ``error`` below.
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, index=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    operations: Mapped[list[ProposedOperation]] = relationship(
        back_populates="proposal",
        cascade="all, delete-orphan",
        order_by="ProposedOperation.seq",
    )


class ProposedOperation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "proposed_operations"
    __table_args__ = (UniqueConstraint("proposal_id", "seq"),)

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("change_proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[ProposedOperationType] = mapped_column(
        pg_enum(ProposedOperationType, "proposed_operation_type"), nullable=False
    )
    # The tool registry's entity key (e.g. "plan_task", "client_assignment") —
    # not a DB table name; drives which service the executor dispatches to.
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID)  # null for a create
    # Human-readable label for the approval card, e.g. a task's title.
    entity_label: Mapped[str | None] = mapped_column(String(200))
    # Per-field {field: {before, after}} diff, computed by the dry run.
    field_changes: Mapped[dict | None] = mapped_column(JSONColumn)
    # Exact args to replay the underlying service call at execution time.
    payload: Mapped[dict | None] = mapped_column(JSONColumn)
    # {"updated_at": "<iso>"} snapshot of the live row at proposal time — a
    # mismatch at approve time means the row changed since this was proposed.
    base_snapshot: Mapped[dict | None] = mapped_column(JSONColumn)
    # Frozen at proposal time from the tool registry — never re-derived at
    # approve time, so a later registry change can't silently reshape an
    # already-pending proposal's authorization requirement.
    required_capability: Mapped[str | None] = mapped_column(String(60))
    # Some entity types (e.g. client assignments) are gated by global user role
    # rather than a per-client capability — frozen at proposal time for the
    # same reason ``required_capability`` is.
    requires_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[ProposedOperationStatus] = mapped_column(
        pg_enum(ProposedOperationStatus, "proposed_operation_status"),
        nullable=False,
        default=ProposedOperationStatus.pending,
    )
    error: Mapped[str | None] = mapped_column(Text)

    proposal: Mapped[ChangeProposal] = relationship(back_populates="operations")
