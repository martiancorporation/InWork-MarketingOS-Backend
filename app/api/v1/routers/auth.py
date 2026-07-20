"""Authentication endpoints: login + logout.

No sign-up — the first admin is seeded (`scripts/seed_data.py`) and further
users are created by an admin via the user-management API. Login mints a
revocable token; logout revokes it server-side (BE-16).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import CurrentToken, CurrentUser, DbSession
from app.core.config import get_settings
from app.core.rate_limit import RateLimit
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive an access token",
    dependencies=[Depends(RateLimit("login", times=10, seconds=60))],
)
def login(data: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    client_host = request.client.host if request.client else None
    user, token = AuthService(db).login(
        data,
        user_agent=request.headers.get("user-agent"),
        ip=client_host,
    )
    expires_in = get_settings().security.access_token_expire_minutes * 60
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserRead.model_validate(user),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current access token (server-side logout)",
)
def logout(_user: CurrentUser, token: CurrentToken, db: DbSession) -> None:
    AuthService(db).logout(token)
