"""AI change-proposal API (v1) — review, approve, or reject a batch of
AI-drafted mutations before anything is written to the database.

- ``GET  /clients/{id}/assistant/proposals/{proposal_id}``          — full detail (the approval card)
- ``POST /clients/{id}/assistant/proposals/{proposal_id}/approve``  — the ONLY mutation trigger
- ``POST /clients/{id}/assistant/proposals/{proposal_id}/reject``   — discard, nothing applied

Every route is client-access-scoped (``ClientService.get_client``/an
inaccessible client returns 404). ``approve`` re-derives the acting user from
the authenticated session — never from anything the AI produced — and
re-validates capability/expiry/staleness before touching a single row; see
``ProposalService`` for the full three-phase commit sequence.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession, RequireClient
from app.schemas.proposal import ChangeProposalRead, RejectProposalRequest
from app.services.proposal_service import ProposalService

router = APIRouter(prefix="/clients/{client_id}/assistant/proposals", tags=["proposals"])


@router.get(
    "/{proposal_id}",
    response_model=ChangeProposalRead,
    summary="Get an AI change proposal (the approval card's detail)",
)
def get_proposal(
    client_id: uuid.UUID, proposal_id: uuid.UUID, db: DbSession, _client: RequireClient
) -> ChangeProposalRead:
    proposal = ProposalService(db).get_proposal(client_id, proposal_id)
    return ChangeProposalRead.model_validate(proposal)


@router.post(
    "/{proposal_id}/approve",
    response_model=ChangeProposalRead,
    summary="Approve and execute an AI change proposal",
)
def approve_proposal(
    client_id: uuid.UUID, proposal_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ChangeProposalRead:
    proposal = ProposalService(db).approve(client_id, proposal_id, user=user)
    return ChangeProposalRead.model_validate(proposal)


@router.post(
    "/{proposal_id}/reject",
    response_model=ChangeProposalRead,
    summary="Reject an AI change proposal — nothing is applied",
)
def reject_proposal(
    client_id: uuid.UUID,
    proposal_id: uuid.UUID,
    data: RejectProposalRequest,
    user: CurrentUser,
    db: DbSession,
) -> ChangeProposalRead:
    proposal = ProposalService(db).reject(client_id, proposal_id, user=user, reason=data.reason)
    return ChangeProposalRead.model_validate(proposal)
