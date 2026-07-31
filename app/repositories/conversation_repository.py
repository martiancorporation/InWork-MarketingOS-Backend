"""Data access for client conversations, messages, and their satellites.

Every query is hard-filtered by ``client_id`` for tenant isolation. Thread
*detail* reads eager-load their messages (and each message's recipients +
attachments) so the service can serve a single thread without N+1s. The thread
*list*, however, only ever needs one summary row per conversation (subject,
latest message, counts, starred/category flags) — ``list_summaries_for_client``
computes that directly in SQL, with filtering and ``LIMIT``/``OFFSET`` pushed to
the database, instead of loading every message of every thread into memory.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased, selectinload

from app.models.conversation import Conversation, Message
from app.models.enums import MessageFolder
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

    def list_summaries_for_client(
        self,
        client_id: uuid.UUID,
        *,
        offset: int,
        limit: int,
        folder: MessageFolder | None = None,
        starred: bool | None = None,
        category: str | None = None,
        search: str | None = None,
    ):
        """One summary row per thread, filtered/paginated entirely in SQL.

        A thread with no messages is excluded (mirrors the old "latest is
        None -> skip" rule) because the join to its latest message finds
        nothing. Returns ``(rows, total)`` where each row is a
        ``sqlalchemy.Row`` with the exact columns ``ConversationListItem``
        needs — no ORM objects, no per-thread message collections loaded.
        """
        latest_message_id = (
            select(Message.id)
            .where(Message.conversation_id == Conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
            .correlate(Conversation)
            .scalar_subquery()
        )
        latest = aliased(Message)

        conditions = [Conversation.client_id == client_id]
        if folder is not None:
            conditions.append(latest.folder == folder)
        if starred:
            conditions.append(
                select(Message.id)
                .where(Message.conversation_id == Conversation.id, Message.is_starred.is_(True))
                .exists()
            )
        if category is not None:
            conditions.append(
                select(Message.id)
                .where(Message.conversation_id == Conversation.id, Message.category == category)
                .exists()
            )
        if search:
            like = f"%{search.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(func.coalesce(Conversation.subject, "")).like(like),
                    select(Message.id)
                    .where(
                        Message.conversation_id == Conversation.id,
                        func.lower(Message.body).like(like),
                    )
                    .exists(),
                )
            )

        message_count = (
            select(func.count(Message.id))
            .where(Message.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )
        is_starred = (
            select(Message.id)
            .where(Message.conversation_id == Conversation.id, Message.is_starred.is_(True))
            .exists()
        )

        base = (
            select(Conversation.id).join(latest, latest.id == latest_message_id).where(*conditions)
        )
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0

        rows = self.db.execute(
            select(
                Conversation.id,
                Conversation.subject,
                Conversation.source,
                Conversation.is_read,
                Conversation.last_message_at,
                Conversation.created_at,
                message_count.label("message_count"),
                latest.body.label("latest_body"),
                latest.folder.label("latest_folder"),
                latest.category.label("latest_category"),
                latest.sender_email.label("latest_sender_email"),
                is_starred.label("is_starred"),
            )
            .join(latest, latest.id == latest_message_id)
            .where(*conditions)
            .order_by(
                Conversation.last_message_at.is_(None),
                Conversation.last_message_at.desc(),
                Conversation.created_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return rows, total

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
            .options(selectinload(Message.recipients), selectinload(Message.attachments))
        )
