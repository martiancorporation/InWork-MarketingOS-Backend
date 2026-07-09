"""Authentication endpoints: signup and login."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import DbSession
from app.core.config import get_settings
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse
from app.schemas.user import UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user, access_token: str) -> TokenResponse:
    expires_in = get_settings().security.access_token_expire_minutes * 60
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=UserRead.model_validate(user),
    )


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and workspace",
)
def signup(data: SignupRequest, db: DbSession) -> TokenResponse:
    user, token = AuthService(db).signup(data)
    return _token_response(user, token)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive an access token",
)
def login(data: LoginRequest, db: DbSession) -> TokenResponse:
    user, token = AuthService(db).login(data)
    return _token_response(user, token)
