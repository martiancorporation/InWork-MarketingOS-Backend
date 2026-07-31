"""Data access for support & feedback tickets.

Owner-scoped, not client-scoped: ``list_page`` filters by ``owner_id`` when
given (the "user" view) or across every ticket when omitted (the "admin"
view) — the caller (service) decides which based on the requester's role.
Repositories never commit; the service owns the transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.models.enums import TicketCategory, TicketPriority, TicketStatus
from app.models.support_ticket import SupportTicket, SupportTicketAttachment
from app.repositories.base import BaseRepository


class SupportTicketRepository(BaseRepository[SupportTicket]):
    model = SupportTicket

    # Attachments' nested `upload` so filename/download_url render with no
    # per-attachment query; replies for the full thread. Bounded by the page
    # size on list, and by construction (max 10 attachments) on detail.
    _loads = (
        selectinload(SupportTicket.attachments).selectinload(SupportTicketAttachment.upload),
        selectinload(SupportTicket.replies),
    )

    def get_detail(self, ticket_id: uuid.UUID) -> SupportTicket | None:
        return self.db.scalar(
            select(SupportTicket).where(SupportTicket.id == ticket_id).options(*self._loads)
        )

    def ticket_number_exists(self, ticket_number: str) -> bool:
        return (
            self.db.scalar(
                select(SupportTicket.id).where(SupportTicket.ticket_number == ticket_number)
            )
            is not None
        )

    def list_page(
        self,
        *,
        owner_id: uuid.UUID | None,
        offset: int,
        limit: int,
        search: str | None = None,
        status: TicketStatus | None = None,
        category: TicketCategory | None = None,
        priority: TicketPriority | None = None,
    ) -> tuple[list[SupportTicket], int]:
        """A bounded page of tickets plus the total matching count.

        ``owner_id=None`` means "every ticket" (the admin view); any other
        value hard-scopes to that user's own tickets (the non-admin view) —
        the one and only place that scoping is enforced at the query level.
        """
        conditions = []
        if owner_id is not None:
            conditions.append(SupportTicket.created_by == owner_id)
        if status is not None:
            conditions.append(SupportTicket.status == status)
        if category is not None:
            conditions.append(SupportTicket.category == category)
        if priority is not None:
            conditions.append(SupportTicket.priority == priority)
        if search:
            like = f"%{search.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(SupportTicket.subject).like(like),
                    func.lower(SupportTicket.ticket_number).like(like),
                    func.lower(SupportTicket.description).like(like),
                )
            )

        total = (
            self.db.scalar(select(func.count()).select_from(SupportTicket).where(*conditions)) or 0
        )
        stmt = (
            select(SupportTicket)
            .where(*conditions)
            .options(*self._loads)
            .order_by(SupportTicket.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all()), int(total)
