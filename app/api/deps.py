"""Shared FastAPI dependencies (auth, DB session, pagination)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AuthError, ForbiddenError
from app.core.pagination import PaginationParams
from app.core.security import TOKEN_TYPE_ACCESS, decode_token, token_id_hash
from app.db.session import get_db
from app.integrations.aws import S3Storage
from app.integrations.storage import Storage
from app.models.client import Client
from app.models.enums import ClientCapability, UserRole
from app.models.user import User
from app.repositories.session_repository import SessionRepository
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

    # Server-side revocation (BE-16): tokens minted at login carry a ``jti`` and a
    # matching session row. If the session is gone (logged out) or expired, the
    # token is dead even though its signature is still valid. Tokens without a
    # ``jti`` stay stateless (backward compatible).
    jti = payload.get("jti")
    if jti is not None:
        session = SessionRepository(db).get_by_token_hash(token_id_hash(str(jti)))
        if session is None:
            raise AuthError("Session has been revoked.")

    user = UserRepository(db).get(payload.get("sub", ""))
    if user is None or not user.is_active:
        raise AuthError("User no longer exists or is inactive.")
    return user


def get_current_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> str:
    """The raw bearer token of the current request (for logout/revocation)."""
    if credentials is None:
        raise AuthError("Authentication required.")
    return credentials.credentials


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
CurrentToken = Annotated[str, Depends(get_current_token)]


def require_capability(
    capability: ClientCapability,
) -> Callable[[uuid.UUID, User, Session], Client]:
    """Build a client-scoped dependency that enforces a per-project capability.

    Returns the resolved ``Client`` so routes can reuse it. Raises 404 for an
    inaccessible client (never leaked) and 403 when the caller can see the client
    but lacks ``capability``. Admins/managers always pass. Use on client-scoped
    routes, e.g. ``Depends(require_capability(ClientCapability.manage_integrations))``.
    """

    def _dependency(
        client_id: uuid.UUID,
        user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Client:
        # Imported lazily to avoid any import-time coupling between the API and
        # service layers.
        from app.services.client_service import ClientService

        return ClientService(db).require_capability(user, client_id, capability)

    return _dependency
