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
from app.services.audit_service import field_changes


def _audit_value(value: object) -> object:
    """JSON-safe scalar for a diff (enum -> ``.value``)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    return getattr(value, "value", value)


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
        user, _changes = self._apply_update_user(user_id, data)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _apply_update_user(self, user_id: uuid.UUID, data: UserUpdate) -> tuple[User, dict | None]:
        """Everything ``update_user`` does short of the commit — the reusable
        core the AI proposal engine dry-runs inside a rolled-back SAVEPOINT
        (see ``ProposalService``) and replays for real at execution time."""
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        tracked = ("name", "role", "is_active")
        before = {f: _audit_value(getattr(user, f)) for f in tracked}
        if data.name is not None:
            user.name = data.name
        if data.role is not None:
            user.role = data.role
        if data.is_active is not None:
            user.is_active = data.is_active
        after = {f: _audit_value(getattr(user, f)) for f in tracked}
        return user, field_changes(before, after)
