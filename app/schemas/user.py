"""User schemas — read plus admin-only create/update."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.schemas.common import ORMModel
from app.schemas.validators import validate_password_strength


class UserRead(ORMModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    """Admin creates a managed user."""

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.user

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return validate_password_strength(value)


class UserUpdate(BaseModel):
    """Admin updates a user's role and/or active state (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int
