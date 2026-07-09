"""User-management use-cases (admin only — enforced at the router)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.core.pagination import PaginationParams
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate, UserListResponse, UserRead, UserUpdate


class UserService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)

    def create_user(self, data: UserCreate) -> User:
        if self.users.email_exists(data.email):
            raise ConflictError("A user with this email already exists.")
        user = User(
            email=data.email.lower(),
            name=data.name,
            password_hash=hash_password(data.password),
            role=data.role,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def list_users(self, pagination: PaginationParams) -> UserListResponse:
        rows, total = self.users.list(offset=pagination.offset, limit=pagination.limit)
        return UserListResponse(
            items=[UserRead.model_validate(u) for u in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def update_user(self, user_id: uuid.UUID, data: UserUpdate) -> User:
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if data.name is not None:
            user.name = data.name
        if data.role is not None:
            user.role = data.role
        if data.is_active is not None:
            user.is_active = data.is_active
        self.db.commit()
        self.db.refresh(user)
        return user
