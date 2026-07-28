"""Schemas for the Project AI assistant ("Ask AI about this project")."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.ai.attachments import MAX_ATTACHMENTS
from app.models.enums import AiRole
from app.schemas.common import ORMModel, StrictModel

_MAX_QUESTION = 4000
_MAX_HISTORY_TURNS = 20


class AssistantChatCreate(StrictModel):
    title: str | None = Field(default=None, max_length=200)
    context_type: str | None = Field(default="project", max_length=40)
    context_key: str | None = Field(default=None, max_length=80)


class AssistantAttachmentRead(BaseModel):
    """A file attached to a chat turn, as the UI needs to render it."""

    upload_id: uuid.UUID
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    #: "image" renders a thumbnail, "file" renders a chip.
    kind: Literal["image", "file"] = "file"
    #: Signed fresh on every read — presigned URLs expire in 15 minutes, so one
    #: persisted at send time would be a dead link by the time the chat reloads.
    download_url: str | None = None


class AssistantMessageRead(ORMModel):
    id: uuid.UUID
    role: AiRole
    content: str
    tokens: int | None = None
    created_at: datetime
    attachments: list[AssistantAttachmentRead] = []


class AssistantChatRead(ORMModel):
    id: uuid.UUID
    title: str | None = None
    context_type: str | None = None
    context_key: str | None = None
    created_at: datetime
    updated_at: datetime


class AssistantChatDetail(AssistantChatRead):
    messages: list[AssistantMessageRead] = []


class AssistantChatListResponse(BaseModel):
    items: list[AssistantChatRead]
    total: int
    page: int
    page_size: int


class AssistantAskRequest(StrictModel):
    """A chat turn: text, attachments, or both.

    ``content`` may be empty *only* when files are attached — dropping a report in
    with no question is a legitimate way to ask "what's in this?", and the composer
    has always allowed sending on attachments alone.
    """

    content: str = Field(default="", max_length=_MAX_QUESTION)
    attachment_upload_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_ATTACHMENTS
    )

    @model_validator(mode="after")
    def _require_something_to_answer(self) -> Self:
        if not self.content.strip() and not self.attachment_upload_ids:
            raise ValueError("Provide a question, an attachment, or both.")
        return self


class AssistantAskResponse(BaseModel):
    message: AssistantMessageRead
    sources: list[str] = []


# ---- Global (platform-wide) assistant ----


class GlobalAssistantTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=_MAX_QUESTION)


class GlobalAssistantAskRequest(StrictModel):
    """A stateless platform-wide question. Optional ``history`` carries prior turns
    for continuity (the endpoint does not persist conversations)."""

    content: str = Field(min_length=1, max_length=_MAX_QUESTION)
    history: list[GlobalAssistantTurn] = Field(default_factory=list, max_length=_MAX_HISTORY_TURNS)


class GlobalAssistantAskResponse(BaseModel):
    answer: str
    # "all clients" for an admin; "N assigned client(s)" otherwise.
    scope: str
    clients_considered: int
    # False when Claude is unconfigured/failed and the deterministic fallback ran.
    ai_generated: bool
