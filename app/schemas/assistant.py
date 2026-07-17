"""Schemas for the Project AI assistant ("Ask AI about this project")."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import AiRole
from app.schemas.common import ORMModel, StrictModel

_MAX_QUESTION = 4000


class AssistantChatCreate(StrictModel):
    title: str | None = Field(default=None, max_length=200)
    context_type: str | None = Field(default="project", max_length=40)
    context_key: str | None = Field(default=None, max_length=80)


class AssistantMessageRead(ORMModel):
    id: uuid.UUID
    role: AiRole
    content: str
    tokens: int | None = None
    created_at: datetime


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
    content: str = Field(min_length=1, max_length=_MAX_QUESTION)


class AssistantAskResponse(BaseModel):
    message: AssistantMessageRead
    sources: list[str] = []
