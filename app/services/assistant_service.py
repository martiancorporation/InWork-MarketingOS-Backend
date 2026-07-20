"""Project AI assistant use-cases ("Ask AI about this project").

Client-access scoping is enforced at the router via ``ClientService.get_client``
(inaccessible client → 404). Repos flush; this service owns the commit. The
question runs through ``ProjectAssistantAgent``, grounded in the client's
intelligence context + RAG store, with a deterministic fallback when Claude is
unconfigured.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.ai.assistant import AssistantStreamPrep, ProjectAssistantAgent
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

logger = logging.getLogger("app.services.assistant")


@dataclass
class StreamContext:
    """Prepared state for a streamed answer (built before the SSE body starts)."""

    agent: ProjectAssistantAgent
    client_id: uuid.UUID
    chat_id: uuid.UUID
    prep: AssistantStreamPrep


def _sse(payload: dict) -> str:
    """One Server-Sent Events frame (``data: {...}\\n\\n``)."""
    return f"data: {json.dumps(payload)}\n\n"


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

    def get_chat_detail(self, client_id: uuid.UUID, chat_id: uuid.UUID) -> AssistantChatDetail:
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
                AiUsageContext(feature=AiFeature.PROJECT_AI, client_id=client_id, user_id=user_id)
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

    def begin_stream(
        self,
        client_id: uuid.UUID,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
    ) -> StreamContext:
        """Validate + persist the user turn and pre-compute the answer prompt while
        the request session is open. Raises ``NotFoundError`` (404) before any
        streaming starts. Call this, then feed the result to ``stream_events``."""
        self._require_chat(client_id, chat_id)
        history = [(m.role.value, m.content) for m in self.chats.list_messages(chat_id)]
        self.chats.add_message(chat_id, AiRole.user, content)
        self.db.commit()

        agent = ProjectAssistantAgent(
            self.db,
            client_id,
            embedder=get_embedder(),
            ai_client=AnthropicClient(
                AiUsageContext(feature=AiFeature.PROJECT_AI, client_id=client_id, user_id=user_id)
            ),
        )
        prep = agent.prepare_stream(content, history=history)
        return StreamContext(agent=agent, client_id=client_id, chat_id=chat_id, prep=prep)

    async def stream_events(self, ctx: StreamContext) -> AsyncIterator[str]:
        """Server-Sent Events for one streamed answer: a ``sources`` frame, then a
        ``delta`` frame per token chunk, then a ``done`` frame with the persisted
        message id + full text. Degrades to the deterministic fallback when Claude
        is unconfigured or the stream fails."""
        prep = ctx.prep
        yield _sse({"type": "sources", "sources": prep.snippets})

        parts: list[str] = []
        if prep.system is None:  # AI unconfigured → stream the deterministic fallback
            parts.append(prep.fallback)
            yield _sse({"type": "delta", "text": prep.fallback})
        else:
            try:
                async for delta in ctx.agent.ai.stream(system=prep.system, prompt=prep.prompt):
                    if delta:
                        parts.append(delta)
                        yield _sse({"type": "delta", "text": delta})
            except Exception:  # transient provider error — degrade, never 500 mid-stream
                logger.warning(
                    "Project assistant stream failed for client %s", ctx.client_id, exc_info=True
                )
                if not parts:
                    parts.append(prep.fallback)
                    yield _sse({"type": "delta", "text": prep.fallback})

        answer = "".join(parts).strip() or prep.fallback
        message_id = self._finalize(ctx.client_id, ctx.chat_id, answer)
        yield _sse({"type": "done", "message_id": str(message_id), "content": answer})

    def _finalize(self, client_id: uuid.UUID, chat_id: uuid.UUID, content: str) -> uuid.UUID:
        """Persist the assembled assistant turn on the request session (same session
        the non-streaming ``ask`` uses)."""
        message = self.chats.add_message(chat_id, AiRole.assistant, content)
        chat = self.chats.get_for_client(client_id, chat_id)
        if chat is not None:
            chat.updated_at = datetime.now(UTC)  # bump so recent chats sort first
        self.db.commit()
        self.db.refresh(message)
        return message.id

    def _require_chat(self, client_id: uuid.UUID, chat_id: uuid.UUID) -> AiChat:
        chat = self.chats.get_for_client(client_id, chat_id)
        if chat is None:
            raise NotFoundError("Chat not found.")
        return chat
