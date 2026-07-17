"""Project AI assistant API (v1) — "Ask AI about this project".

- ``GET    /clients/{id}/assistant/chats``                    — list project chats
- ``POST   /clients/{id}/assistant/chats``                    — start a chat
- ``GET    /clients/{id}/assistant/chats/{chat_id}``          — chat + messages
- ``POST   /clients/{id}/assistant/chats/{chat_id}/messages`` — ask a question (AI reply)
- ``DELETE /clients/{id}/assistant/chats/{chat_id}``          — delete a chat

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404. The assistant is grounded in
the client's intelligence profile + RAG knowledge and degrades to a deterministic
reply when Claude is unconfigured. The ask endpoint is rate-limited (paid-AI).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination
from app.core.rate_limit import RateLimit
from app.schemas.assistant import (
    AssistantAskRequest,
    AssistantAskResponse,
    AssistantChatCreate,
    AssistantChatDetail,
    AssistantChatListResponse,
    AssistantChatRead,
)
from app.schemas.common import MessageResponse
from app.services.assistant_service import AssistantService
from app.services.client_service import ClientService

router = APIRouter(prefix="/clients/{client_id}/assistant", tags=["assistant"])


@router.get(
    "/chats", response_model=AssistantChatListResponse, summary="List project AI chats"
)
def list_chats(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    context_type: str | None = Query(None, max_length=40, description="e.g. 'project'"),
) -> AssistantChatListResponse:
    ClientService(db).get_client(user, client_id)  # 404 if not accessible
    return AssistantService(db).list_chats(
        client_id, pagination=pagination, context_type=context_type
    )


@router.post(
    "/chats",
    response_model=AssistantChatRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a project AI chat",
)
def create_chat(
    client_id: uuid.UUID, data: AssistantChatCreate, user: CurrentUser, db: DbSession
) -> AssistantChatRead:
    ClientService(db).get_client(user, client_id)
    chat = AssistantService(db).create_chat(client_id, user.id, data)
    return AssistantChatRead.model_validate(chat)


@router.get(
    "/chats/{chat_id}",
    response_model=AssistantChatDetail,
    summary="Get a chat with its messages",
)
def get_chat(
    client_id: uuid.UUID, chat_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> AssistantChatDetail:
    ClientService(db).get_client(user, client_id)
    return AssistantService(db).get_chat_detail(client_id, chat_id)


@router.post(
    "/chats/{chat_id}/messages",
    response_model=AssistantAskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ask the project AI a question",
    dependencies=[Depends(RateLimit("assistant_ask", times=30, seconds=60))],
)
async def ask(
    client_id: uuid.UUID,
    chat_id: uuid.UUID,
    data: AssistantAskRequest,
    user: CurrentUser,
    db: DbSession,
) -> AssistantAskResponse:
    ClientService(db).get_client(user, client_id)
    return await AssistantService(db).ask(client_id, chat_id, user.id, data.content)


@router.delete(
    "/chats/{chat_id}", response_model=MessageResponse, summary="Delete a chat"
)
def delete_chat(
    client_id: uuid.UUID, chat_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageResponse:
    ClientService(db).get_client(user, client_id)
    AssistantService(db).delete_chat(client_id, chat_id)
    return MessageResponse(detail="Chat deleted.")
