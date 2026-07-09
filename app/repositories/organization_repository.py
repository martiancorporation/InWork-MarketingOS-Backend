"""Data access for organizations and memberships."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.organization import Organization, OrganizationMember
from app.repositories.base import BaseRepository


class OrganizationRepository(BaseRepository[Organization]):
    model = Organization

    def slug_exists(self, slug: str) -> bool:
        return (
            self.db.scalar(select(Organization.id).where(Organization.slug == slug))
            is not None
        )

    def primary_for_user(self, user_id: uuid.UUID) -> Organization | None:
        """The user's default organization (earliest membership).

        Multi-org switching is a future concern; for now every user has exactly
        one org created at signup.
        """
        return self.db.scalar(
            select(Organization)
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(OrganizationMember.created_at.asc())
            .limit(1)
        )
