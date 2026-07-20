"""Platform-wide AI assistant API (v1) — "Ask AI about my portfolio".

- ``POST /assistant/ask`` — ask a cross-client question (stateless AI reply)

NOT client-scoped: the assistant reasons over every client the caller can access
(all clients for an admin, only assigned clients otherwise). That scoping is
enforced in ``GlobalAssistantService``, so any authenticated user may call this;
they simply see a smaller portfolio. Degrades to a deterministic summary when
Claude is unconfigured. Rate-limited as a paid-AI route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession
from app.core.rate_limit import RateLimit
from app.schemas.assistant import (
    GlobalAssistantAskRequest,
    GlobalAssistantAskResponse,
)
from app.services.global_assistant_service import GlobalAssistantService

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post(
    "/ask",
    response_model=GlobalAssistantAskResponse,
    summary="Ask the platform-wide AI assistant a cross-client question",
    dependencies=[Depends(RateLimit("global_assistant_ask", times=30, seconds=60))],
)
async def ask_global_assistant(
    data: GlobalAssistantAskRequest, user: CurrentUser, db: DbSession
) -> GlobalAssistantAskResponse:
    return await GlobalAssistantService(db).ask(user, data.content, data.history)
