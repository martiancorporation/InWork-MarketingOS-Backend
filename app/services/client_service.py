"""Client read/list use-cases with role-based access scoping.

Admins see every client. Non-admins (manager/user) see only clients assigned to
them; an unassigned client is reported as *not found* so its existence never
leaks.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.pagination import PaginationParams
from app.core.request_context import set_audit_changes
from app.models.assignment import ClientAssignment
from app.models.client import Client
from app.models.enums import ClientCapability, ClientStatus, UserRole
from app.models.user import User
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.client_repository import ClientRepository
from app.schemas.client import ClientListItem, ClientListResponse, ClientUpdate
from app.services.audit_service import field_changes


def _audit_value(value):
    """JSON-safe representation of a field value for the audit diff."""
    return getattr(value, "value", value)


# The full per-client capability set. Admins (globally) and managers (on their
# assigned clients) implicitly hold this whole set; a pre-RBAC assignment (NULL
# capabilities) is also treated as the full set so nothing breaks.
ALL_CAPABILITIES: frozenset[ClientCapability] = frozenset(ClientCapability)


def capabilities_from_stored(
    stored: list[str] | None,
) -> frozenset[ClientCapability]:
    """Turn a stored JSON capability list into a capability set.

    ``None`` (legacy assignments) → the full set. An explicit list is parsed,
    ignoring any unknown values; the per-client ``admin`` capability expands to
    the full set.
    """
    if stored is None:
        return ALL_CAPABILITIES
    caps: set[ClientCapability] = set()
    for value in stored:
        try:
            caps.add(ClientCapability(value))
        except ValueError:
            continue  # tolerate values retired from the enum
    if ClientCapability.admin in caps:
        return ALL_CAPABILITIES
    return frozenset(caps)


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

    def update_client(self, client_id: uuid.UUID, data: ClientUpdate) -> Client:
        """Admin edit of status / basic profile fields (partial). Scoping is
        enforced at the router via the ``AdminUser`` dependency."""
        client = self.clients.get(client_id)
        if client is None:
            raise NotFoundError("Client not found.")
        fields = data.model_fields_set
        tracked = ("name", "business_type", "industry", "website", "location", "status")
        before = {f: _audit_value(getattr(client, f)) for f in tracked}
        for attr in tracked:
            if attr in fields:
                setattr(client, attr, getattr(data, attr))
        after = {f: _audit_value(getattr(client, f)) for f in tracked}
        # Record the before/after diff so the audit log shows what changed.
        changes = field_changes(before, after)
        if changes:
            set_audit_changes(changes)
        self.db.commit()
        self.db.refresh(client)
        return client

    def _can_access(self, user: User, client_id: uuid.UUID) -> bool:
        return user.role == UserRole.admin or self.assignments.exists(client_id, user.id)

    # ---- granular per-project RBAC (BE-03) ---- #

    def effective_capabilities(
        self, user: User, client_id: uuid.UUID
    ) -> frozenset[ClientCapability]:
        """The capabilities ``user`` holds on ``client_id``.

        Admins hold every capability on every client; managers hold every
        capability on their assigned clients; a plain user holds exactly the set
        recorded on their assignment (a ``NULL`` set — legacy — counts as full).
        An unassigned non-admin holds none.
        """
        if user.role == UserRole.admin:
            return ALL_CAPABILITIES
        assignment: ClientAssignment | None = self.assignments.get(client_id, user.id)
        if assignment is None:
            return frozenset()
        if user.role == UserRole.manager:
            return ALL_CAPABILITIES
        return capabilities_from_stored(assignment.capabilities)

    def require_capability(
        self, user: User, client_id: uuid.UUID, capability: ClientCapability
    ) -> Client:
        """Load a client only if ``user`` may access it AND holds ``capability``.

        Mirrors ``get_client``: an inaccessible client is 404 (never leaked). A
        client the user *can* see but lacks the capability for is 403 — the
        resource's existence is already known, so hiding it adds nothing.
        """
        client = self.get_client(user, client_id)  # 404 if inaccessible
        if capability not in self.effective_capabilities(user, client_id):
            raise ForbiddenError(
                f"This action requires the '{capability.value}' capability on this client."
            )
        return client

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
            onboarding_step=c.onboarding_step,
            spend=float(c.spend_total),
            leads=c.leads_total,
            cpl=float(c.cpl),
            created_at=c.created_at,
        )
