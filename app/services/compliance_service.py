"""Compliance-register use-cases.

The register is additive: the active entries are the effective ruleset the
intelligence pipeline compiles into client directives. Any change (add / edit /
deactivate / delete) — and the explicit "sync" — enqueues an intelligence rebuild
in the SAME transaction (outbox), debounced so bursts coalesce, exactly as the
onboarding flow does. Client-access scoping is enforced at the router.
Repositories flush; this service owns the commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.models.client import Client
from app.models.compliance import ComplianceEntry
from app.models.enums import ComplianceKind, IntelJobType
from app.repositories.compliance_repository import ComplianceRepository
from app.schemas.compliance import (
    ComplianceEntryCreate,
    ComplianceEntryRead,
    ComplianceEntryUpdate,
    ComplianceListResponse,
)
from app.schemas.intelligence import IntelligenceStatus
from app.services.intelligence.intelligence_service import IntelligenceService
from app.services.intelligence.job_queue import JobQueue


class ComplianceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.entries = ComplianceRepository(db)

    def list_entries(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        kind: ComplianceKind | None = None,
        active_only: bool = False,
    ) -> ComplianceListResponse:
        rows, total = self.entries.list_for_client(
            client_id,
            kind=kind,
            active_only=active_only,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        items = [ComplianceEntryRead.model_validate(e) for e in rows]
        return ComplianceListResponse(
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_entry(self, client_id: uuid.UUID, entry_id: uuid.UUID) -> ComplianceEntry:
        entry = self.entries.get_for_client(client_id, entry_id)
        if entry is None:
            raise NotFoundError("Compliance entry not found.")
        return entry

    def create_entry(
        self, client_id: uuid.UUID, data: ComplianceEntryCreate, *, author_id: uuid.UUID
    ) -> ComplianceEntry:
        entry = ComplianceEntry(
            client_id=client_id, kind=data.kind, text=data.text.strip(), author_id=author_id
        )
        self.entries.add(entry)
        self._enqueue_rebuild(client_id)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def update_entry(
        self, client_id: uuid.UUID, entry_id: uuid.UUID, data: ComplianceEntryUpdate
    ) -> ComplianceEntry:
        entry = self.get_entry(client_id, entry_id)
        fields = data.model_fields_set
        if "kind" in fields and data.kind is not None:
            entry.kind = data.kind
        if "text" in fields and data.text is not None:
            entry.text = data.text.strip()
        if "is_active" in fields and data.is_active is not None:
            entry.is_active = data.is_active
        self._enqueue_rebuild(client_id)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def delete_entry(self, client_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        entry = self.get_entry(client_id, entry_id)
        self.db.delete(entry)
        self._enqueue_rebuild(client_id)
        self.db.commit()

    def sync(self, client_id: uuid.UUID) -> IntelligenceStatus:
        """Force the effective ruleset into the AI now (no debounce)."""
        self._enqueue_rebuild(client_id, debounce_seconds=0)
        self.db.commit()
        client = self.db.get(Client, client_id)
        return IntelligenceService(self.db).status(client)

    def _enqueue_rebuild(self, client_id: uuid.UUID, *, debounce_seconds: int = 5) -> None:
        JobQueue(self.db).enqueue(
            client_id,
            IntelJobType.incremental.value,
            changed_keys=["compliance"],
            debounce_seconds=debounce_seconds,
        )
