"""Project AI assistant API (v1) — "Ask AI about this project".

- ``GET    /clients/{id}/assistant/chats``                    — list project chats
- ``POST   /clients/{id}/assistant/chats``                    — start a chat
- ``GET    /clients/{id}/assistant/chats/{chat_id}``          — chat + messages
- ``POST   /clients/{id}/assistant/chats/{chat_id}/messages`` — ask a question (AI reply)
- ``POST   /clients/{id}/assistant/chats/{chat_id}/messages/stream``               — same, streamed (SSE)
- ``POST   /clients/{id}/assistant/chats/{chat_id}/turn``          — natural-language command turn (proposes changes)
- ``POST   /clients/{id}/assistant/chats/{chat_id}/turn/stream``   — same, streamed (SSE)
- ``POST   /clients/{id}/assistant/chats/{chat_id}/messages/{mid}/approve-plan``   — approve a chat-drafted content plan
- ``POST   /clients/{id}/assistant/chats/{chat_id}/messages/{mid}/reject-plan``    — discard a chat-drafted content plan
- ``DELETE /clients/{id}/assistant/chats/{chat_id}``          — delete a chat

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404. The assistant is grounded in
the client's intelligence profile + RAG knowledge and degrades to a deterministic
reply when the AI provider is unconfigured. The ask endpoint is rate-limited (paid-AI).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentUser,
    DbSession,
    Pagination,
    RequireClient,
    StorageDep,
    require_capability,
)
from app.core.pagination import PaginationParams
from app.core.rate_limit import RateLimit
from app.models.client import Client
from app.models.enums import ClientCapability
from app.schemas.assistant import (
    AssistantAskRequest,
    AssistantAskResponse,
    AssistantChatCreate,
    AssistantChatDetail,
    AssistantChatListResponse,
    AssistantChatRead,
    AssistantMessageRead,
    AssistantRejectPlanRequest,
)
from app.schemas.common import MessageResponse
from app.schemas.proposal import CommandTurnRequest, CommandTurnResponse
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
    ctx = await service.begin_stream(
        client_id,
        chat_id,
        user,
        data.content,
        attachment_upload_ids=data.attachment_upload_ids,
    )
    return StreamingResponse(
        service.stream_events(ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/chats/{chat_id}/turn",
    response_model=CommandTurnResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Natural-language command turn — proposes changes, never applies them",
    dependencies=[Depends(RateLimit("assistant_ask", times=30, seconds=60))],
)
async def turn(
    client_id: uuid.UUID,
    chat_id: uuid.UUID,
    data: CommandTurnRequest,
    user: CurrentUser,
    db: DbSession,
    _client: RequireClient,
) -> CommandTurnResponse:
    """Runs the AI command layer for one chat turn. Any mutation the model
    attempted comes back as a ``proposal`` on the response — nothing is
    written until a human calls ``POST .../proposals/{id}/approve``."""
    return await AssistantService(db).run_command_turn(client_id, chat_id, user, data.content)


@router.post(
    "/chats/{chat_id}/turn/stream",
    summary="Natural-language command turn — streamed token-by-token (SSE)",
    dependencies=[Depends(RateLimit("assistant_ask", times=30, seconds=60))],
)
async def turn_stream(
    client_id: uuid.UUID,
    chat_id: uuid.UUID,
    data: CommandTurnRequest,
    user: CurrentUser,
    db: DbSession,
    _client: RequireClient,
) -> StreamingResponse:
    """Server-Sent Events: a ``delta`` frame per token of the model's own
    reply, a ``tool_progress`` frame as each tool call is dispatched, then a
    ``done`` frame with the persisted message id, full reply text, and any
    staged ``proposal`` — nothing is written to the database until a human
    calls ``POST .../proposals/{id}/approve``. Access + chat-existence are
    checked (404) before the stream opens, same as ``ask_stream``."""
    service = AssistantService(db)
    ctx = await service.begin_command_stream(client_id, chat_id, user, data.content)
    return StreamingResponse(
        service.stream_command_events(ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/chats/{chat_id}/messages/{message_id}/approve-plan",
    response_model=AssistantMessageRead,
    summary="Approve a content-plan draft the AI generated inside this chat",
)
def approve_plan(
    client_id: uuid.UUID,
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    # Same responsibility gate as the manual "Generate with AI" flow (BE-03).
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_calendar))],
) -> AssistantMessageRead:
    return AssistantService(db).approve_plan_draft(client_id, chat_id, message_id, actor=user)


@router.post(
    "/chats/{chat_id}/messages/{message_id}/reject-plan",
    response_model=AssistantMessageRead,
    summary="Discard a content-plan draft the AI generated inside this chat",
)
def reject_plan(
    client_id: uuid.UUID,
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    data: AssistantRejectPlanRequest,
    user: CurrentUser,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_calendar))],
) -> AssistantMessageRead:
    return AssistantService(db).reject_plan_draft(
        client_id, chat_id, message_id, data.reason, actor=user
    )


@router.delete("/chats/{chat_id}", response_model=MessageResponse, summary="Delete a chat")
def delete_chat(
    client_id: uuid.UUID, chat_id: uuid.UUID, db: DbSession, _client: RequireClient
) -> MessageResponse:
    AssistantService(db).delete_chat(client_id, chat_id)
    return MessageResponse(detail="Chat deleted.")
