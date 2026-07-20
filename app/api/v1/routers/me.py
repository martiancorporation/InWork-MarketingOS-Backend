"""Cross-client "what's on you" endpoints (BE-04).

The per-user view that aggregates outstanding work across every client the
caller can access — the data behind the app's red-dot badges.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession, Pagination
from app.schemas.me import MePendingResponse
from app.services.me_service import MeService

router = APIRouter(prefix="/me", tags=["me"])


@router.get(
    "/pending",
    response_model=MePendingResponse,
    summary="My pending work across all accessible clients (per-client counts)",
)
def my_pending(
    user: CurrentUser, db: DbSession, pagination: Pagination
) -> MePendingResponse:
    return MeService(db).pending(user, pagination=pagination)
