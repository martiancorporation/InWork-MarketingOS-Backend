"""Authentication use-cases: signup and login.

Owns the transaction boundary. Signup provisions the user, their organization,
and an owner membership atomically — a new user always lands in a usable
workspace.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import AuthError, ConflictError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.enums import UserRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, SignupRequest
from app.utils.slug import slugify, unique_slug


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.orgs = OrganizationRepository(db)

    def signup(self, data: SignupRequest) -> tuple[User, str]:
        if self.users.email_exists(data.email):
            raise ConflictError("An account with this email already exists.")

        org_name = data.organization_name or f"{data.name}'s Workspace"
        org = Organization(
            name=org_name,
            slug=unique_slug(slugify(org_name, fallback="workspace"), exists=self.orgs.slug_exists),
        )
        user = User(
            email=data.email.lower(),
            name=data.name,
            password_hash=hash_password(data.password),
            role=UserRole.admin,  # first user owns their workspace
        )
        self.db.add_all([org, user])
        self.db.flush()  # assign ids
        self.db.add(
            OrganizationMember(organization_id=org.id, user_id=user.id, role=UserRole.admin)
        )
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
