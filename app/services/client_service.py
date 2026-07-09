"""Client read/list use-cases with role-based access scoping.

Admins see every client. Non-admins (manager/user) see only clients assigned to
them; an unassigned client is reported as *not found* so its existence never
leaks.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.models.client import Client
from app.models.enums import ClientStatus, UserRole
from app.models.user import User
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.client_repository import ClientRepository
from app.schemas.client import ClientListItem, ClientListResponse


class ClientService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.clients = ClientRepository(db)
        self.assignments = AssignmentRepository(db)

    def list_clients(
        self,
        user: User,
        *,
        pagination: PaginationParams,
        search: str | None = None,
        status: ClientStatus | None = None,
    ) -> ClientListResponse:
        if user.role == UserRole.admin:
            rows, total = self.clients.list_all(
                offset=pagination.offset, limit=pagination.limit, search=search, status=status
            )
        else:
            rows, total = self.clients.list_assigned(
                user.id,
                offset=pagination.offset,
                limit=pagination.limit,
                search=search,
                status=status,
            )
        return ClientListResponse(
            items=[self._to_item(c) for c in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_client(self, user: User, client_id: uuid.UUID) -> Client:
        client = self.clients.get(client_id)
        if client is None or not self._can_access(user, client_id):
            # 404 (not 403) so non-admins can't probe which clients exist.
            raise NotFoundError("Client not found.")
        return client

    def _can_access(self, user: User, client_id: uuid.UUID) -> bool:
        return user.role == UserRole.admin or self.assignments.exists(client_id, user.id)

    @staticmethod
    def _to_item(c: Client) -> ClientListItem:
        return ClientListItem(
            id=c.id,
            slug=c.slug,
            name=c.name,
            business_type=c.business_type,
            industry=c.industry,
            website=c.website,
            location=c.location,
            status=c.status,
            spend=float(c.spend_total),
            leads=c.leads_total,
            cpl=float(c.cpl),
            created_at=c.created_at,
        )
