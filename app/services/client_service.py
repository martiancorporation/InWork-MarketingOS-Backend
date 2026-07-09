"""Client read/list use-cases (scoped to the caller's organization)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.pagination import PaginationParams
from app.models.client import Client
from app.models.enums import ClientStatus
from app.models.organization import Organization
from app.models.user import User
from app.repositories.client_repository import ClientRepository
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.client import ClientListItem, ClientListResponse


class ClientService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.clients = ClientRepository(db)
        self.orgs = OrganizationRepository(db)

    def resolve_org(self, user: User) -> Organization:
        org = self.orgs.primary_for_user(user.id)
        if org is None:
            raise BadRequestError("Your account is not linked to an organization.")
        return org

    def list_clients(
        self,
        user: User,
        *,
        pagination: PaginationParams,
        search: str | None = None,
        status: ClientStatus | None = None,
    ) -> ClientListResponse:
        org = self.resolve_org(user)
        rows, total = self.clients.list_for_org(
            org.id,
            offset=pagination.offset,
            limit=pagination.limit,
            search=search,
            status=status,
        )
        items = [
            ClientListItem(
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
            for c in rows
        ]
        return ClientListResponse(
            items=items, total=total, page=pagination.page, page_size=pagination.page_size
        )

    def get_client(self, user: User, client_id: uuid.UUID) -> Client:
        org = self.resolve_org(user)
        client = self.clients.get_for_org(org.id, client_id)
        if client is None:
            raise NotFoundError("Client not found.")
        return client
