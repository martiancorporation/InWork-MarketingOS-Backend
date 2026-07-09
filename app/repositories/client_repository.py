"""Data access for clients."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select

from app.models.client import Client
from app.models.enums import ClientStatus
from app.repositories.base import BaseRepository


class ClientRepository(BaseRepository[Client]):
    model = Client

    def slug_exists(self, organization_id: uuid.UUID, slug: str) -> bool:
        return (
            self.db.scalar(
                select(Client.id).where(
                    Client.organization_id == organization_id, Client.slug == slug
                )
            )
            is not None
        )

    def get_for_org(self, organization_id: uuid.UUID, client_id: uuid.UUID) -> Client | None:
        return self.db.scalar(
            select(Client).where(
                Client.id == client_id, Client.organization_id == organization_id
            )
        )

    def list_for_org(
        self,
        organization_id: uuid.UUID,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        status: ClientStatus | None = None,
    ) -> tuple[list[Client], int]:
        """Return a page of clients plus the total count matching the filters."""
        conditions = [Client.organization_id == organization_id]
        if search:
            pattern = f"%{search.strip()}%"
            conditions.append(
                or_(Client.name.ilike(pattern), Client.industry.ilike(pattern))
            )
        if status is not None:
            conditions.append(Client.status == status)

        total = self.db.scalar(
            select(func.count()).select_from(Client).where(*conditions)
        )
        rows = list(
            self.db.scalars(
                select(Client)
                .where(*conditions)
                .order_by(Client.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return rows, int(total or 0)
