"""AI change-proposal schemas — the natural-language command surface's
propose -> approve -> execute contract (see ``ProposalService``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ProposalStatus, ProposedOperationStatus, ProposedOperationType
from app.schemas.common import MAX_TEXT, ORMModel, StrictModel


class ProposedOperationRead(ORMModel):
    id: uuid.UUID
    seq: int
    operation_type: ProposedOperationType
    entity_type: str
    entity_id: uuid.UUID | None = None
    entity_label: str | None = None
    field_changes: dict | None = None
    required_capability: str | None = None
    requires_admin: bool = False
    status: ProposedOperationStatus
    error: str | None = None


class ChangeProposalRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    chat_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID | None = None
    status: ProposalStatus
    raw_request: str | None = None
    summary: str | None = None
    error: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    approved_by_user_id: uuid.UUID | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    operations: list[ProposedOperationRead] = []


class CommandTurnRequest(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_TEXT)


class CommandTurnResponse(BaseModel):
    """One AI chat turn's result. ``proposal`` is set only when the turn staged
    at least one mutation — the frontend renders the approval card from it.
    Nothing in ``proposal`` has been written to the database yet."""

    chat_id: uuid.UUID
    message_id: uuid.UUID
    reply: str
    proposal: ChangeProposalRead | None = None


class RejectProposalRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=MAX_TEXT)
