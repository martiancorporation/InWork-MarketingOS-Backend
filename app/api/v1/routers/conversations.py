"""Conversations API (v1) — a shared per-client inbox.

- ``GET    /clients/{id}/conversations``                    — list threads (folder/category/search)
- ``POST   /clients/{id}/conversations``                    — compose a new thread
- ``GET    /clients/{id}/conversations/{cid}``              — full thread
- ``PATCH  /clients/{id}/conversations/{cid}``              — mark read/unread
- ``DELETE /clients/{id}/conversations/{cid}``              — delete thread
- ``POST   /clients/{id}/conversations/{cid}/messages``     — reply
- ``PATCH  /clients/{id}/conversations/{cid}/messages/{mid}`` — move folder / star / relabel

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404. Any user who can see the
client can work its inbox.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination
from app.models.enums import MessageFolder
from app.schemas.common import MessageResponse
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListResponse,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessageRead,
    MessageUpdate,
)
from app.services.client_service import ClientService
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/clients/{client_id}/conversations", tags=["conversations"])


@router.get("", response_model=ConversationListResponse, summary="List conversations")
def list_conversations(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    folder: MessageFolder | None = Query(None, description="Filter by the latest message's folder"),
    starred: bool | None = Query(None, description="Only threads with a starred message"),
    category: str | None = Query(None, description="Filter by message category label"),
    search: str | None = Query(None, description="Match subject or message body"),
) -> ConversationListResponse:
    ClientService(db).get_client(user, client_id)
    return ConversationService(db).list_conversations(
        client_id,
        pagination=pagination,
        folder=folder,
        starred=starred,
        category=category,
        search=search,
    )


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new conversation",
)
def create_conversation(
    client_id: uuid.UUID, data: ConversationCreate, user: CurrentUser, db: DbSession
) -> ConversationRead:
    ClientService(db).get_client(user, client_id)
    conv = ConversationService(db).create_conversation(
        client_id, data, sender_user_id=user.id
    )
    return ConversationRead.model_validate(conv)


@router.get(
    "/{conversation_id}", response_model=ConversationRead, summary="Get a conversation thread"
)
def get_conversation(
    client_id: uuid.UUID, conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ConversationRead:
    ClientService(db).get_client(user, client_id)
    conv = ConversationService(db).get_conversation(client_id, conversation_id)
    return ConversationRead.model_validate(conv)


@router.patch(
    "/{conversation_id}", response_model=ConversationRead, summary="Mark a thread read/unread"
)
def update_conversation(
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    data: ConversationUpdate,
    user: CurrentUser,
    db: DbSession,
) -> ConversationRead:
    ClientService(db).get_client(user, client_id)
    conv = ConversationService(db).update_conversation(client_id, conversation_id, data)
    return ConversationRead.model_validate(conv)


@router.delete(
    "/{conversation_id}", response_model=MessageResponse, summary="Delete a conversation"
)
def delete_conversation(
    client_id: uuid.UUID, conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageResponse:
    ClientService(db).get_client(user, client_id)
    ConversationService(db).delete_conversation(client_id, conversation_id)
    return MessageResponse(detail="Conversation deleted.")


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Reply to a conversation",
)
def add_message(
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    data: MessageCreate,
    user: CurrentUser,
    db: DbSession,
) -> MessageRead:
    ClientService(db).get_client(user, client_id)
    message = ConversationService(db).add_message(
        client_id, conversation_id, data, sender_user_id=user.id
    )
    return MessageRead.model_validate(message)


@router.patch(
    "/{conversation_id}/messages/{message_id}",
    response_model=MessageRead,
    summary="Move to a folder / star / relabel a message",
)
def update_message(
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    data: MessageUpdate,
    user: CurrentUser,
    db: DbSession,
) -> MessageRead:
    ClientService(db).get_client(user, client_id)
    message = ConversationService(db).update_message(
        client_id, conversation_id, message_id, data
    )
    return MessageRead.model_validate(message)
