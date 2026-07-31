"""Support & feedback ticket use-cases.

Object-level authorization lives here (this is a global, owner-scoped
resource — there's no ``ClientService`` to delegate to): a non-admin only
ever sees/touches their own tickets, and an inaccessible-to-them ticket 404s
rather than 403s, so ids can't be probed (same rule as ``UploadService``).
Once a ticket *is* visible to the caller, an action they hold but aren't
allowed to take (a non-admin changing ``status``, or deleting a ticket that's
moved past ``open``) is a 403 — the resource's existence is already known, so
hiding it adds nothing.

Repositories flush; this service owns the commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.pagination import PaginationParams
from app.integrations.storage import Storage
from app.models.enums import TicketCategory, TicketPriority, TicketStatus, UserRole
from app.models.support_ticket import SupportTicket, SupportTicketAttachment, SupportTicketReply
from app.models.user import User
from app.repositories.support_ticket_repository import SupportTicketRepository
from app.schemas.support_ticket import (
    SupportTicketCreate,
    SupportTicketListItem,
    SupportTicketListResponse,
    SupportTicketRead,
    SupportTicketUpdate,
    TicketAttachmentRead,
    TicketReplyRead,
)
from app.services.upload_service import UploadService
from app.utils.download_link import upload_permalink

_TICKET_NUMBER_ATTEMPTS = 5


def _generate_ticket_number() -> str:
    return f"TCK-{uuid.uuid4().hex[:8].upper()}"


class SupportTicketService:
    def __init__(self, db: Session, storage: Storage) -> None:
        self.db = db
        self.storage = storage
        self.tickets = SupportTicketRepository(db)

    # ---- create ---- #

    def create_ticket(self, user: User, data: SupportTicketCreate) -> SupportTicketRead:
        attachments = self._resolve_attachments(user, data.attachment_upload_ids)

        ticket_number = self._unique_ticket_number()
        ticket = SupportTicket(
            ticket_number=ticket_number,
            subject=data.subject,
            category=data.category,
            description=data.description,
            priority=data.priority,
            status=TicketStatus.open,
            created_by=user.id,
            attachments=attachments,
        )
        self.tickets.add(ticket)
        try:
            self.db.commit()
        except Exception as exc:  # pragma: no cover - unique-key race
            self.db.rollback()
            raise ConflictError("Could not create the ticket — please retry.") from exc
        reloaded = self.tickets.get_detail(ticket.id)
        assert reloaded is not None
        return self._to_read(reloaded)

    def _unique_ticket_number(self) -> str:
        for _ in range(_TICKET_NUMBER_ATTEMPTS):
            candidate = _generate_ticket_number()
            if not self.tickets.ticket_number_exists(candidate):
                return candidate
        raise ConflictError("Could not generate a unique ticket number — please retry.")

    # ---- read ---- #

    def list_tickets(
        self,
        user: User,
        *,
        pagination: PaginationParams,
        search: str | None = None,
        status: TicketStatus | None = None,
        category: TicketCategory | None = None,
        priority: TicketPriority | None = None,
    ) -> SupportTicketListResponse:
        # The one and only scoping decision: a non-admin's owner_id is always
        # their own id (they cannot widen this via any filter parameter); an
        # admin's is None (every ticket).
        owner_id = None if user.role == UserRole.admin else user.id
        rows, total = self.tickets.list_page(
            owner_id=owner_id,
            offset=pagination.offset,
            limit=pagination.limit,
            search=search,
            status=status,
            category=category,
            priority=priority,
        )
        items = [
            SupportTicketListItem(
                id=t.id,
                ticket_number=t.ticket_number,
                subject=t.subject,
                category=t.category,
                priority=t.priority,
                status=t.status,
                created_by=t.created_by,
                created_at=t.created_at,
                updated_at=t.updated_at,
                attachment_count=len(t.attachments),
                reply_count=len(t.replies),
            )
            for t in rows
        ]
        return SupportTicketListResponse(
            items=items, total=total, page=pagination.page, page_size=pagination.page_size
        )

    def get_ticket(self, user: User, ticket_id: uuid.UUID) -> SupportTicketRead:
        return self._to_read(self._load_accessible(user, ticket_id))

    # ---- update ---- #

    def update_ticket(
        self, user: User, ticket_id: uuid.UUID, data: SupportTicketUpdate
    ) -> SupportTicketRead:
        ticket = self._load_accessible(user, ticket_id)
        is_admin = user.role == UserRole.admin
        fields = data.model_fields_set

        # Check every authorization rule before mutating anything, so a
        # request that fails partway never leaves a half-applied ticket.
        if "status" in fields and data.status is not None and not is_admin:
            raise ForbiddenError("Only an admin can change a ticket's status.")

        if "subject" in fields and data.subject is not None:
            ticket.subject = data.subject
        if "category" in fields and data.category is not None:
            ticket.category = data.category
        if "description" in fields and data.description is not None:
            ticket.description = data.description
        if "priority" in fields and data.priority is not None:
            ticket.priority = data.priority
        if "status" in fields and data.status is not None:
            ticket.status = data.status

        if "attachment_upload_ids" in fields and data.attachment_upload_ids is not None:
            ticket.attachments = self._resolve_attachments(user, data.attachment_upload_ids)

        if data.reply:
            ticket.replies.append(
                SupportTicketReply(
                    author_id=user.id,
                    author_role="admin" if is_admin else "user",
                    message=data.reply,
                )
            )

        self._commit("Could not update the ticket.")
        reloaded = self.tickets.get_detail(ticket.id)
        assert reloaded is not None
        return self._to_read(reloaded)

    # ---- delete ---- #

    def delete_ticket(self, user: User, ticket_id: uuid.UUID) -> None:
        ticket = self._load_accessible(user, ticket_id)
        if user.role != UserRole.admin and ticket.status != TicketStatus.open:
            raise ForbiddenError(
                "This ticket is already being worked on — only an admin can delete it now."
            )
        self.db.delete(ticket)
        self._commit("Could not delete the ticket.")

    # ---- helpers ---- #

    def _load_accessible(self, user: User, ticket_id: uuid.UUID) -> SupportTicket:
        ticket = self.tickets.get_detail(ticket_id)
        # 404 (not 403) for someone else's ticket so ids can't be probed —
        # same rule as UploadService._load_owned.
        if ticket is None or (user.role != UserRole.admin and ticket.created_by != user.id):
            raise NotFoundError("Support ticket not found.")
        return ticket

    def _to_read(self, ticket: SupportTicket) -> SupportTicketRead:
        """Build the response model by hand rather than ``model_validate(ticket)``:
        an attachment's ``filename``/``content_type``/``size_bytes``/``download_url``
        live on the related ``Upload`` row (under different attribute names), not on
        ``SupportTicketAttachment`` itself, so plain ORM-mode field lookup can't
        reach them.
        """
        attachments = [
            TicketAttachmentRead(
                id=a.id,
                upload_id=a.upload_id,
                filename=a.upload.original_filename,
                content_type=a.upload.content_type,
                size_bytes=a.upload.size_bytes,
                download_url=upload_permalink(a.upload.id, epoch=a.upload.link_epoch),
            )
            for a in ticket.attachments
        ]
        replies = [TicketReplyRead.model_validate(r) for r in ticket.replies]
        return SupportTicketRead(
            id=ticket.id,
            ticket_number=ticket.ticket_number,
            subject=ticket.subject,
            category=ticket.category,
            description=ticket.description,
            priority=ticket.priority,
            status=ticket.status,
            created_by=ticket.created_by,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            attachments=attachments,
            replies=replies,
        )

    def _resolve_attachments(
        self, user: User, upload_ids: list[uuid.UUID]
    ) -> list[SupportTicketAttachment]:
        """Validate each id via the owner-scoped upload lookup (admin sees
        all, a plain user only their own — the exact rule
        ``UploadService.get`` already enforces) before attaching it."""
        upload_service = UploadService(self.db, self.storage)
        attachments = []
        for upload_id in upload_ids:
            upload_service.get(user, upload_id)  # 404s if missing/not the caller's
            attachments.append(SupportTicketAttachment(upload_id=upload_id))
        return attachments

    def _commit(self, error_message: str) -> None:
        try:
            self.db.commit()
        except Exception as exc:  # pragma: no cover - constraint violation etc.
            self.db.rollback()
            raise ConflictError(error_message) from exc
