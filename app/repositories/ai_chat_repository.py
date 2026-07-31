"""Data access for the Project AI assistant chats + messages.

Chats are hard-scoped by ``client_id`` (a per-client shared surface — any user who
can access the client sees its project chats). Repos flush, never commit — the
service owns the transaction. Message ``created_at`` is stamped app-side (µs
precision) so a user/assistant pair added in one request orders reliably even on
SQLite's second-resolution ``now()``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models.ai import AiChat, AiChatMessage
from app.models.enums import AiRole
from app.repositories.base import BaseRepository


class AiChatRepository(BaseRepository[AiChat]):
    model = AiChat

    def create_chat(
        self,
        client_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        title: str | None = None,
        context_type: str | None = "project",
        context_key: str | None = None,
    ) -> AiChat:
        chat = AiChat(
            client_id=client_id,
            user_id=user_id,
            title=title,
            context_type=context_type,
            context_key=context_key,
        )
        self.db.add(chat)
        self.db.flush()
        return chat

    def get_for_client(self, client_id: uuid.UUID, chat_id: uuid.UUID) -> AiChat | None:
        return self.db.scalar(
            select(AiChat).where(AiChat.id == chat_id, AiChat.client_id == client_id)
        )

    def list_for_client(
        self,
        client_id: uuid.UUID,
        *,
        context_type: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[AiChat], int]:
        cond = [AiChat.client_id == client_id]
        if context_type:
            cond.append(AiChat.context_type == context_type)
        total = self.db.scalar(select(func.count()).select_from(AiChat).where(*cond)) or 0
        query = select(AiChat).where(*cond).order_by(AiChat.updated_at.desc())
        if limit is not None:
            query = query.offset(offset).limit(limit)
        return list(self.db.scalars(query)), total

    def add_message(
        self,
        chat_id: uuid.UUID,
        role: AiRole,
        content: str,
        *,
        tokens: int | None = None,
        meta: dict | None = None,
    ) -> AiChatMessage:
        message = AiChatMessage(
            chat_id=chat_id,
            role=role,
            meta=meta,
            content=content,
            tokens=tokens,
            created_at=datetime.now(UTC),
        )
        self.db.add(message)
        self.db.flush()
        return message

    def list_messages(self, chat_id: uuid.UUID, *, limit: int | None = None) -> list[AiChatMessage]:
        """Full (or ``limit``-capped) message history, oldest first.

        Used to build the LLM prompt's conversation history — ``limit`` caps
        how many of the *most recent* messages are sent (see
        ``ProjectAssistantAgent``/``AssistantService``), since sending an
        ever-growing transcript on every turn costs more tokens each time with
        no bound. Not used to serve the paginated chat-detail view — see
        ``list_messages_page`` for that.
        """
        query = select(AiChatMessage).where(AiChatMessage.chat_id == chat_id)
        if limit is None:
            return list(self.db.scalars(query.order_by(AiChatMessage.created_at)))
        # Take the most recent `limit` by sorting newest-first with a DB-side
        # LIMIT, then reverse in Python back to chronological order.
        recent = list(self.db.scalars(query.order_by(AiChatMessage.created_at.desc()).limit(limit)))
        recent.reverse()
        return recent

    def list_messages_page(
        self, chat_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[AiChatMessage], int]:
        """A bounded page of a chat's messages (oldest first), for the
        paginated chat-detail API — as opposed to ``list_messages``, which
        feeds the LLM prompt."""
        cond = AiChatMessage.chat_id == chat_id
        total = self.db.scalar(select(func.count()).select_from(AiChatMessage).where(cond)) or 0
        rows = self.db.scalars(
            select(AiChatMessage)
            .where(cond)
            .order_by(AiChatMessage.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), int(total)

    def delete_chat(self, chat: AiChat) -> None:
        self.db.delete(chat)
        self.db.flush()
