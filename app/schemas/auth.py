"""Authentication request/response schemas.

There is no public sign-up: the first admin is provisioned by the seed script
(`scripts/seed_data.py`), and all other users are created by an admin via the
user-management API. Login is the only authentication entry point.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until the access token expires
    user: UserRead
