"""Authentication request/response schemas.

There is no public sign-up: the first admin is provisioned by the seed script
(`scripts/seed_data.py`), and all other users are created by an admin via the
user-management API. Login is the only authentication entry point.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import StrictModel
from app.schemas.user import UserRead


class LoginRequest(StrictModel):
    email: EmailStr
    # Upper bound is defensive, not a hashing constraint (the password is
    # SHA-256 pre-hashed before bcrypt, see app/core/security.py) — it just
    # stops a client from forcing the server to hash a multi-MB string on
    # every login attempt.
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until the access token expires
    user: UserRead
