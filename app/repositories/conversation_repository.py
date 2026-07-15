"""Data access for client conversations, messages, and their satellites.

Every query is hard-filtered by ``client_id`` for tenant isolation. Threads are
returned newest-activity first with their messages (and each message's recipients
+ attachments) eager-loaded so the service can summarize/serve without N+1s.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, Message
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    _loads = (
        selectinload(Conversation.messages).selectinload(Message.recipients),
        selectinload(Conversation.messages).selectinload(Message.attachments),
    )

    def get_for_client(
        self, client_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation | None:
        return self.db.scalar(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.client_id == client_id,
            )
            .options(*self._loads)
        )

    def list_for_client(self, client_id: uuid.UUID) -> list[Conversation]:
        """All threads for a client, newest activity first (nulls last)."""
        rows = list(
            self.db.scalars(
                select(Conversation)
                .where(Conversation.client_id == client_id)
                .options(*self._loads)
            ).all()
        )
        rows.sort(
            key=lambda c: (c.last_message_at is not None, c.last_message_at, c.created_at),
            reverse=True,
        )
        return rows

    def get_message(
        self, client_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID
    ) -> Message | None:
        return self.db.scalar(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
                Conversation.client_id == client_id,
            )
            .options(
                selectinload(Message.recipients), selectinload(Message.attachments)
            )
        )
