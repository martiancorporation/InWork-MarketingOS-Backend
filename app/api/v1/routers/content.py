"""Content review API (v1) — pre-publish AI check (brand voice + SEO + compliance).

- ``POST /clients/{id}/content/review`` — review a draft caption/post before a
  human approves it.

Client-access-scoped via ``ClientService.get_client`` (admin or assigned user);
inaccessible client → 404. Compliance (banned/required terms) and an SEO score are
deterministic; the brand-voice judgment uses Claude when configured and degrades
gracefully otherwise. Rate-limited (paid-AI route).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.ai.content_review import ContentReviewAgent
from app.api.deps import CurrentUser, DbSession
from app.core.rate_limit import RateLimit
from app.schemas.content import ContentReviewReport, ContentReviewRequest
from app.services.client_service import ClientService

router = APIRouter(prefix="/clients/{client_id}/content", tags=["content"])


@router.post(
    "/review",
    response_model=ContentReviewReport,
    summary="AI pre-publish review of a draft (brand voice + SEO + compliance)",
    dependencies=[Depends(RateLimit("content_review", times=30, seconds=60))],
)
async def review_content(
    client_id: uuid.UUID,
    data: ContentReviewRequest,
    user: CurrentUser,
    db: DbSession,
) -> ContentReviewReport:
    ClientService(db).get_client(user, client_id)  # 404 if not accessible
    return await ContentReviewAgent(db, client_id).review(data.content, platform=data.platform)
