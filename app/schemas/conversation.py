"""Conversation schemas: a shared per-client inbox of threads + messages.

Mirrors the web conversations page (folders, categories, reading pane, reply/
compose). ``folder``/``category``/``is_starred`` live on the message; ``is_read``
is a thread-level flag.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import ConversationSource, MessageFolder, RecipientKind
from app.schemas.common import MAX_TEXT, ORMModel

# --------------------------------------------------------------------------- #
# Recipients / attachments
# --------------------------------------------------------------------------- #


class RecipientIn(BaseModel):
    email: EmailStr = Field(max_length=255)
    kind: RecipientKind = RecipientKind.to


class RecipientRead(ORMModel):
    email: str
    kind: RecipientKind


class AttachmentRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #


class MessageRead(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_user_id: uuid.UUID | None = None
    sender_email: str | None = None
    folder: MessageFolder
    category: str | None = None
    is_starred: bool
    body: str
    created_at: datetime
    added_to_source_at: datetime | None = None
    knowledge_source_id: uuid.UUID | None = None
    recipients: list[RecipientRead] = []
    attachments: list[AttachmentRead] = []


class MessageCreate(BaseModel):
    """Reply within a thread. Defaults to an outbound (sent) message."""

    body: str = Field(min_length=1, max_length=MAX_TEXT)
    folder: MessageFolder = MessageFolder.sent
    category: str | None = Field(None, max_length=40)
    recipients: list[RecipientIn] = Field(default=[], max_length=100)


class MessageUpdate(BaseModel):
    """Message-level actions: move folder, (un)star, relabel. Partial."""

    folder: MessageFolder | None = None
    is_starred: bool | None = None
    category: str | None = Field(None, max_length=40)


# --------------------------------------------------------------------------- #
# Conversations
# --------------------------------------------------------------------------- #


class ConversationCreate(BaseModel):
    """Compose a new thread with its first message."""

    subject: str | None = Field(None, max_length=255)
    body: str = Field(min_length=1, max_length=MAX_TEXT)
    category: str | None = Field(None, max_length=40)
    source: ConversationSource = ConversationSource.email
    folder: MessageFolder = MessageFolder.sent
    recipients: list[RecipientIn] = Field(default=[], max_length=100)


class ConversationUpdate(BaseModel):
    is_read: bool | None = None


class ConversationListItem(BaseModel):
    """Thread summary for the mail list — derived from the latest message."""

    id: uuid.UUID
    subject: str | None = None
    source: ConversationSource
    is_read: bool
    last_message_at: datetime | None = None
    message_count: int
    preview: str
    latest_folder: MessageFolder | None = None
    latest_category: str | None = None
    latest_sender_email: str | None = None
    is_starred: bool


class ConversationListResponse(BaseModel):
    items: list[ConversationListItem]
    total: int
    page: int = 1
    page_size: int = 20


class ConversationRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    subject: str | None = None
    source: ConversationSource
    is_read: bool
    last_message_at: datetime | None = None
    created_at: datetime
    messages: list[MessageRead] = []
