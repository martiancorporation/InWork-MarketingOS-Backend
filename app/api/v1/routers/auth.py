"""Authentication endpoints: login.

No sign-up — the first admin is seeded (`scripts/seed_data.py`) and further
users are created by an admin via the user-management API.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession
from app.core.config import get_settings
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive an access token",
)
def login(data: LoginRequest, db: DbSession) -> TokenResponse:
    user, token = AuthService(db).login(data)
    expires_in = get_settings().security.access_token_expire_minutes * 60
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserRead.model_validate(user),
    )
