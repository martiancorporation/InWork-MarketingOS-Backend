"""Authentication use-case: login.

There is no sign-up. The first admin is provisioned by the seed script
(`scripts/seed_data.py`); further users are created by an admin via the
user-management API.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import AuthError
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)

    def login(self, data: LoginRequest) -> tuple[User, str]:
        user = self.users.get_by_email(data.email)
        # Same generic error whether the email is unknown or the password is
        # wrong, so the endpoint never reveals which emails have accounts.
        if user is None or not verify_password(data.password, user.password_hash):
            raise AuthError("Invalid email or password.")
        if not user.is_active:
            raise AuthError("This account is disabled.")

        user.last_login_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(user)
        return user, create_access_token(user.id)
