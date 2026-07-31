"""Support & feedback ticket schemas.

A global, owner-scoped resource (see ``app/models/support_ticket.py``) — no
``client_id`` anywhere here. Attach files by uploading first via
``POST /uploads`` and passing the returned id in ``attachment_upload_ids``;
this feature doesn't reinvent upload handling.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import TicketCategory, TicketPriority, TicketStatus
from app.schemas.common import MAX_TEXT, ORMModel, StrictModel

_MAX_ATTACHMENTS = 10


class TicketAttachmentRead(ORMModel):
    id: uuid.UUID
    upload_id: uuid.UUID
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    #: Re-signed on every read (a permanent, redirect-through-us link — see
    #: app/utils/download_link.py) — never persisted, so it can't go stale.
    download_url: str | None = None


class TicketReplyRead(ORMModel):
    id: uuid.UUID
    author_id: uuid.UUID | None = None
    author_role: str
    message: str
    created_at: datetime


class SupportTicketCreate(StrictModel):
    subject: str = Field(min_length=1, max_length=200)
    category: TicketCategory
    description: str = Field(min_length=1, max_length=MAX_TEXT)
    priority: TicketPriority = TicketPriority.medium
    #: Ids from ``POST /uploads`` — each must belong to the caller.
    attachment_upload_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=_MAX_ATTACHMENTS
    )


class SupportTicketUpdate(StrictModel):
    """Partial update — only fields present in the body are applied
    (``model_fields_set``), so changing just the status never clobbers the
    rest of the ticket. Exposed at ``PUT /support-tickets/{id}`` per the API
    contract, but behaves like the rest of this app's autosave-style PATCH
    endpoints rather than a full-replace PUT — resending the entire ticket on
    every small edit isn't a contract any client should need to honor.

    ``status`` may only be changed by an admin (a reporter can edit their
    ticket's content but not decide it's resolved) — enforced in
    ``SupportTicketService.update``, 403 if a non-admin includes it.
    """

    subject: str | None = Field(default=None, min_length=1, max_length=200)
    category: TicketCategory | None = None
    description: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    priority: TicketPriority | None = None
    status: TicketStatus | None = None  # admin-only
    #: Appends one reply to the thread (both the reporter and an admin may
    #: reply); omit to leave the thread untouched.
    reply: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    #: When present, REPLACES the ticket's full attachment set (each id must
    #: belong to the caller); omit to leave attachments untouched.
    attachment_upload_ids: list[uuid.UUID] | None = Field(default=None, max_length=_MAX_ATTACHMENTS)


class SupportTicketRead(ORMModel):
    id: uuid.UUID
    ticket_number: str
    subject: str
    category: TicketCategory
    description: str
    priority: TicketPriority
    status: TicketStatus
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    attachments: list[TicketAttachmentRead] = []
    replies: list[TicketReplyRead] = []


class SupportTicketListItem(ORMModel):
    id: uuid.UUID
    ticket_number: str
    subject: str
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    attachment_count: int = 0
    reply_count: int = 0


class SupportTicketListResponse(BaseModel):
    items: list[SupportTicketListItem]
    total: int
    page: int
    page_size: int
