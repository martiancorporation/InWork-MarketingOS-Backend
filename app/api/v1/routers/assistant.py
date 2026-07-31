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
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession, Pagination, RequireClient, StorageDep
from app.core.pagination import PaginationParams
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

router = APIRouter(prefix="/clients/{client_id}/assistant", tags=["assistant"])


@router.get("/chats", response_model=AssistantChatListResponse, summary="List project AI chats")
def list_chats(
    client_id: uuid.UUID,
    db: DbSession,
    pagination: Pagination,
    _client: RequireClient,
    context_type: str | None = Query(None, max_length=40, description="e.g. 'project'"),
) -> AssistantChatListResponse:
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
    client_id: uuid.UUID,
    data: AssistantChatCreate,
    user: CurrentUser,
    db: DbSession,
    _client: RequireClient,
) -> AssistantChatRead:
    chat = AssistantService(db).create_chat(client_id, user.id, data)
    return AssistantChatRead.model_validate(chat)


@router.get(
    "/chats/{chat_id}",
    response_model=AssistantChatDetail,
    summary="Get a chat with its messages",
)
def get_chat(
    client_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    page: int = Query(1, ge=1, description="1-based page number"),
    # Defaults to the max allowed (not the shared Pagination dependency's 20)
    # — the client renders `messages` as the complete thread and re-fetches
    # this route after every send, so a low default would make a chat that
    # crosses the page boundary appear to silently drop its newest messages
    # (including the one just sent) rather than growing. 100 comfortably
    # covers a real working conversation; only a genuinely huge thread needs
    # an explicit `page=2` to see further back.
    page_size: int = Query(100, ge=1, le=100, description="Items per page (max 100)"),
) -> AssistantChatDetail:
    pagination = PaginationParams(page=page, page_size=page_size)
    return AssistantService(db).get_chat_detail(client_id, chat_id, pagination=pagination)


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
    storage: StorageDep,
    _client: RequireClient,
) -> AssistantAskResponse:
    return await AssistantService(db).ask(
        client_id,
        chat_id,
        user,
        data.content,
        attachment_upload_ids=data.attachment_upload_ids,
        storage=storage,
    )


@router.post(
    "/chats/{chat_id}/messages/stream",
    summary="Ask the project AI — streamed token-by-token (SSE, ChatGPT-style)",
    dependencies=[Depends(RateLimit("assistant_ask", times=30, seconds=60))],
)
async def ask_stream(
    client_id: uuid.UUID,
    chat_id: uuid.UUID,
    data: AssistantAskRequest,
    user: CurrentUser,
    db: DbSession,
    _client: RequireClient,
) -> StreamingResponse:
    """Server-Sent Events: a ``sources`` frame, a ``delta`` frame per token chunk,
    then a ``done`` frame with the persisted message id + full text. Access +
    chat-existence are checked (404) before the stream opens."""
    service = AssistantService(db)
    ctx = service.begin_stream(
        client_id,
        chat_id,
        user.id,
        data.content,
        attachment_upload_ids=data.attachment_upload_ids,
    )
    return StreamingResponse(
        service.stream_events(ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/chats/{chat_id}", response_model=MessageResponse, summary="Delete a chat")
def delete_chat(
    client_id: uuid.UUID, chat_id: uuid.UUID, db: DbSession, _client: RequireClient
) -> MessageResponse:
    AssistantService(db).delete_chat(client_id, chat_id)
    return MessageResponse(detail="Chat deleted.")
