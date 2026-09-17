"""Data access for AI change proposals (the propose -> approve -> execute engine)."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update

from app.models.enums import ProposalStatus
from app.models.proposal import ChangeProposal
from app.repositories.base import BaseRepository


class ProposalRepository(BaseRepository[ChangeProposal]):
    model = ChangeProposal

    def get_for_client(self, client_id: uuid.UUID, proposal_id: uuid.UUID) -> ChangeProposal | None:
        return self.db.scalar(
            select(ChangeProposal).where(
                ChangeProposal.id == proposal_id, ChangeProposal.client_id == client_id
            )
        )

    def claim_for_execution(self, proposal_id: uuid.UUID) -> int:
        """Compare-and-swap ``pending_approval -> executing``.

        A plain ``UPDATE ... WHERE status = 'pending_approval'`` is atomic
        under normal transaction isolation — a concurrent or duplicate approve
        call that loses the race updates 0 rows and must read back the (by
        then further along) proposal rather than re-executing it. The caller
        commits this immediately, so the claim is durable before any operation
        is applied.
        """
        result = self.db.execute(
            update(ChangeProposal)
            .where(
                ChangeProposal.id == proposal_id,
                ChangeProposal.status == ProposalStatus.pending_approval,
            )
            .values(status=ProposalStatus.executing)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]
