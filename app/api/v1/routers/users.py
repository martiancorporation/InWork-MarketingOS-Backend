"""User-management endpoints (admin only)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import AdminUser, DbSession, Pagination
from app.schemas.user import UserCreate, UserListResponse, UserRead, UserUpdate
from app.services.user_service import UserService

# Every route requires an admin (the AdminUser dependency in each signature).
router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user (admin)",
)
def create_user(data: UserCreate, _admin: AdminUser, db: DbSession) -> UserRead:
    return UserRead.model_validate(UserService(db).create_user(data))


@router.get("", response_model=UserListResponse, summary="List users (admin)")
def list_users(_admin: AdminUser, db: DbSession, pagination: Pagination) -> UserListResponse:
    return UserService(db).list_users(pagination)


@router.patch(
    "/{user_id}", response_model=UserRead, summary="Update a user's role / status (admin)"
)
def update_user(
    user_id: uuid.UUID, data: UserUpdate, _admin: AdminUser, db: DbSession
) -> UserRead:
    return UserRead.model_validate(UserService(db).update_user(user_id, data))
