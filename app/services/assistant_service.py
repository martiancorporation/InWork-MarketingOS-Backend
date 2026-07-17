"""Project AI assistant use-cases ("Ask AI about this project").

Client-access scoping is enforced at the router via ``ClientService.get_client``
(inaccessible client → 404). Repos flush; this service owns the commit. The
question runs through ``ProjectAssistantAgent``, grounded in the client's
intelligence context + RAG store, with a deterministic fallback when Claude is
unconfigured.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.ai.assistant import ProjectAssistantAgent
from app.ai.features import AiFeature
from app.ai.usage import AiUsageContext
from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.embeddings import get_embedder
from app.models.ai import AiChat
from app.models.enums import AiRole
from app.repositories.ai_chat_repository import AiChatRepository
from app.schemas.assistant import (
    AssistantAskResponse,
    AssistantChatCreate,
    AssistantChatDetail,
    AssistantChatListResponse,
    AssistantChatRead,
    AssistantMessageRead,
)


class AssistantService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.chats = AiChatRepository(db)

    def list_chats(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        context_type: str | None = None,
    ) -> AssistantChatListResponse:
        rows, total = self.chats.list_for_client(
            client_id,
            context_type=context_type,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return AssistantChatListResponse(
            items=[AssistantChatRead.model_validate(c) for c in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def create_chat(
        self, client_id: uuid.UUID, user_id: uuid.UUID, data: AssistantChatCreate
    ) -> AiChat:
        chat = self.chats.create_chat(
            client_id,
            user_id,
            title=data.title,
            context_type=data.context_type or "project",
            context_key=data.context_key,
        )
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def get_chat_detail(
        self, client_id: uuid.UUID, chat_id: uuid.UUID
    ) -> AssistantChatDetail:
        chat = self._require_chat(client_id, chat_id)
        messages = self.chats.list_messages(chat_id)
        return AssistantChatDetail(
            id=chat.id,
            title=chat.title,
            context_type=chat.context_type,
            context_key=chat.context_key,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            messages=[AssistantMessageRead.model_validate(m) for m in messages],
        )

    def delete_chat(self, client_id: uuid.UUID, chat_id: uuid.UUID) -> None:
        chat = self._require_chat(client_id, chat_id)
        self.chats.delete_chat(chat)
        self.db.commit()

    async def ask(
        self,
        client_id: uuid.UUID,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
    ) -> AssistantAskResponse:
        chat = self._require_chat(client_id, chat_id)
        history = [(m.role.value, m.content) for m in self.chats.list_messages(chat_id)]
        self.chats.add_message(chat_id, AiRole.user, content)

        agent = ProjectAssistantAgent(
            self.db,
            client_id,
            embedder=get_embedder(),
            ai_client=AnthropicClient(
                AiUsageContext(
                    feature=AiFeature.PROJECT_AI, client_id=client_id, user_id=user_id
                )
            ),
        )
        answer, sources = await agent.answer(content, history=history)

        assistant_msg = self.chats.add_message(chat_id, AiRole.assistant, answer)
        chat.updated_at = datetime.now(UTC)  # bump so recent chats sort first
        self.db.commit()
        self.db.refresh(assistant_msg)
        return AssistantAskResponse(
            message=AssistantMessageRead.model_validate(assistant_msg), sources=sources
        )

    def _require_chat(self, client_id: uuid.UUID, chat_id: uuid.UUID) -> AiChat:
        chat = self.chats.get_for_client(client_id, chat_id)
        if chat is None:
            raise NotFoundError("Chat not found.")
        return chat
