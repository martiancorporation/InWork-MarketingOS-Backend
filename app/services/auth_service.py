"""Authentication use-cases: bootstrap signup and login.

Signup is a one-time bootstrap: it provisions the *first* user as an admin, then
closes permanently. Afterward, admins create users via the user-management API.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import AuthError, ForbiddenError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, SignupRequest


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)

    def signup(self, data: SignupRequest) -> tuple[User, str]:
        if self.users.count() > 0:
            raise ForbiddenError(
                "Sign-up is closed. Ask an administrator to create your account."
            )
        user = User(
            email=data.email.lower(),
            name=data.name,
            password_hash=hash_password(data.password),
            role=UserRole.admin,  # the first user bootstraps the system as admin
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user, create_access_token(user.id)

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
