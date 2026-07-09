"""Data access for clients (single-domain; access scoping is applied here)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.sql import Select

from app.models.assignment import ClientAssignment
from app.models.client import Client
from app.models.enums import ClientStatus
from app.repositories.base import BaseRepository


class ClientRepository(BaseRepository[Client]):
    model = Client

    def slug_exists(self, slug: str) -> bool:
        return self.db.scalar(select(Client.id).where(Client.slug == slug)) is not None

    def _apply_filters(
        self, stmt: Select, search: str | None, status: ClientStatus | None
    ) -> Select:
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(Client.name.ilike(pattern), Client.industry.ilike(pattern))
            )
        if status is not None:
            stmt = stmt.where(Client.status == status)
        return stmt

    def list_all(
        self, *, offset: int, limit: int, search: str | None = None, status: ClientStatus | None = None
    ) -> tuple[list[Client], int]:
        """Every client — for admins."""
        base = self._apply_filters(select(Client), search, status)
        return self._paginate(base, offset, limit)

    def list_assigned(
        self,
        user_id: uuid.UUID,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        status: ClientStatus | None = None,
    ) -> tuple[list[Client], int]:
        """Only clients assigned to ``user_id`` — for non-admins."""
        base = self._apply_filters(
            select(Client).join(
                ClientAssignment, ClientAssignment.client_id == Client.id
            ).where(ClientAssignment.user_id == user_id),
            search,
            status,
        )
        return self._paginate(base, offset, limit)

    def _paginate(self, base: Select, offset: int, limit: int) -> tuple[list[Client], int]:
        total = int(
            self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        rows = list(
            self.db.scalars(
                base.order_by(Client.created_at.desc()).offset(offset).limit(limit)
            ).all()
        )
        return rows, total
