"""Conversation (shared inbox) use-cases: threads, messages, folders, stars.

Client-access scoping is enforced at the router (``ClientService.get_client``)
before any method here runs; every query is additionally hard-filtered by
``client_id``. Repositories flush; this service owns the commit.

List filtering (folder/starred/category/search) is applied in Python over the
client's threads — an agency inbox is bounded, and it keeps the thread-summary
logic (derived from each thread's latest message) in one place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.models.conversation import Conversation, Message, MessageRecipient
from app.models.enums import ConversationSource, MessageFolder
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListItem,
    ConversationListResponse,
    ConversationUpdate,
    MessageCreate,
    MessageUpdate,
    RecipientIn,
)


class ConversationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.conversations = ConversationRepository(db)

    # ---- reads --------------------------------------------------------- #

    def list_conversations(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        folder: MessageFolder | None = None,
        starred: bool | None = None,
        category: str | None = None,
        search: str | None = None,
    ) -> ConversationListResponse:
        rows = self.conversations.list_for_client(client_id)
        items: list[ConversationListItem] = []
        q = (search or "").strip().lower()
        for conv in rows:
            latest = self._latest(conv)
            if latest is None:
                continue
            is_starred = any(m.is_starred for m in conv.messages)
            if folder is not None and latest.folder != folder:
                continue
            if starred and not is_starred:
                continue
            if category is not None and not any(
                (m.category or "") == category for m in conv.messages
            ):
                continue
            if q and not (
                (conv.subject or "").lower().find(q) >= 0
                or any(q in (m.body or "").lower() for m in conv.messages)
            ):
                continue
            items.append(
                ConversationListItem(
                    id=conv.id,
                    subject=conv.subject,
                    source=conv.source,
                    is_read=conv.is_read,
                    last_message_at=conv.last_message_at,
                    message_count=len(conv.messages),
                    preview=(latest.body or "")[:140],
                    latest_folder=latest.folder,
                    latest_category=latest.category,
                    latest_sender_email=latest.sender_email,
                    is_starred=is_starred,
                )
            )
        # Rows are already newest-activity first; filtering preserves that order.
        # Slice the matched set to the requested page (bounds the response).
        total = len(items)
        page_items = items[pagination.offset : pagination.offset + pagination.limit]
        return ConversationListResponse(
            items=page_items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_conversation(
        self, client_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation:
        conv = self.conversations.get_for_client(client_id, conversation_id)
        if conv is None:
            raise NotFoundError("Conversation not found.")
        return conv

    # ---- writes -------------------------------------------------------- #

    def create_conversation(
        self, client_id: uuid.UUID, data: ConversationCreate, *, sender_user_id: uuid.UUID
    ) -> Conversation:
        now = _now()
        conv = Conversation(
            client_id=client_id,
            subject=data.subject,
            source=data.source or ConversationSource.email,
            is_read=True,  # we composed it, so it's read
            last_message_at=now,
        )
        message = Message(
            sender_user_id=sender_user_id,
            folder=data.folder,
            category=data.category,
            body=data.body,
            recipients=self._recipients(data.recipients),
        )
        conv.messages.append(message)
        self.conversations.add(conv)
        self.db.commit()
        return self.get_conversation(client_id, conv.id)

    def add_message(
        self,
        client_id: uuid.UUID,
        conversation_id: uuid.UUID,
        data: MessageCreate,
        *,
        sender_user_id: uuid.UUID,
    ) -> Message:
        conv = self.get_conversation(client_id, conversation_id)
        message = Message(
            conversation_id=conv.id,
            sender_user_id=sender_user_id,
            folder=data.folder,
            category=data.category,
            body=data.body,
            recipients=self._recipients(data.recipients),
        )
        conv.messages.append(message)
        conv.last_message_at = _now()
        conv.is_read = True  # replying implies the thread is read
        self.db.commit()
        reloaded = self.conversations.get_message(client_id, conv.id, message.id)
        assert reloaded is not None
        return reloaded

    def update_conversation(
        self, client_id: uuid.UUID, conversation_id: uuid.UUID, data: ConversationUpdate
    ) -> Conversation:
        conv = self.get_conversation(client_id, conversation_id)
        if "is_read" in data.model_fields_set and data.is_read is not None:
            conv.is_read = data.is_read
        self.db.commit()
        return self.get_conversation(client_id, conv.id)

    def update_message(
        self,
        client_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        data: MessageUpdate,
    ) -> Message:
        message = self.conversations.get_message(client_id, conversation_id, message_id)
        if message is None:
            raise NotFoundError("Message not found.")
        fields = data.model_fields_set
        if "folder" in fields and data.folder is not None:
            message.folder = data.folder
        if "is_starred" in fields and data.is_starred is not None:
            message.is_starred = data.is_starred
        if "category" in fields:
            message.category = data.category
        self.db.commit()
        reloaded = self.conversations.get_message(client_id, conversation_id, message_id)
        assert reloaded is not None
        return reloaded

    def delete_conversation(
        self, client_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> None:
        conv = self.get_conversation(client_id, conversation_id)
        self.db.delete(conv)
        self.db.commit()

    # ---- helpers ------------------------------------------------------- #

    @staticmethod
    def _latest(conv: Conversation) -> Message | None:
        if not conv.messages:
            return None
        return max(conv.messages, key=lambda m: m.created_at)

    @staticmethod
    def _recipients(recipients: list[RecipientIn]) -> list[MessageRecipient]:
        return [MessageRecipient(email=r.email, kind=r.kind) for r in recipients]


def _now() -> datetime:
    return datetime.now(UTC)
