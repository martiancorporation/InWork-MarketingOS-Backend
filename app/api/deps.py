"""Shared FastAPI dependencies (auth, DB session, pagination)."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AuthError, ForbiddenError
from app.core.pagination import PaginationParams
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.session import get_db
from app.integrations.aws import S3Storage
from app.integrations.storage import Storage
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import UserRepository

_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise AuthError("Authentication required.")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid authentication token.") from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise AuthError("Invalid token type.")

    user = UserRepository(db).get(payload.get("sub", ""))
    if user is None or not user.is_active:
        raise AuthError("User no longer exists or is inactive.")
    return user


def get_current_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """RBAC guard: allow only admins. Use on admin-only endpoints."""
    if user.role != UserRole.admin:
        raise ForbiddenError("Administrator privileges are required.")
    return user


def get_storage() -> Storage:
    """Provide the app-wide object-storage backend (S3).

    Overridable in tests via ``app.dependency_overrides[get_storage]``. The S3
    client itself is created lazily on first call, so this is cheap to build.
    """
    return S3Storage(get_settings().storage)


# Reusable annotated aliases so routers read cleanly.
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(get_current_admin)]
Pagination = Annotated[PaginationParams, Depends()]
StorageDep = Annotated[Storage, Depends(get_storage)]
