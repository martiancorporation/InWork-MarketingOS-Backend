"""Client intelligence API (async profile + RAG directives).

- ``GET  /clients/{id}/intelligence``            — current summary + directives
- ``GET  /clients/{id}/intelligence/status``     — building | ready | failed
- ``GET  /clients/{id}/intelligence/versions``   — version history
- ``GET  /clients/{id}/intelligence/versions/{v}`` — a specific version
- ``POST /clients/{id}/intelligence/rebuild``    — force a full rebuild (admin)
- ``POST /clients/{id}/directives/{did}/resolve``— resolve a conflict (admin)
- ``GET  /clients/{id}/context``                 — debug: what agents receive

Every route is client-access-scoped (admin or assigned user); an inaccessible
client returns 404, never revealing its existence.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.integrations.embeddings import get_embedder
from app.schemas.intelligence import (
    ClientContextResponse,
    DirectiveRead,
    IntelligenceResponse,
    IntelligenceStatus,
    ProfileVersionItem,
    RetrievedChunk,
)
from app.services.client_service import ClientService
from app.services.intelligence.context_service import ContextService
from app.services.intelligence.intelligence_service import IntelligenceService

router = APIRouter(prefix="/clients", tags=["intelligence"])


@router.get(
    "/{client_id}/intelligence",
    response_model=IntelligenceResponse,
    summary="Current client intelligence profile + directives",
)
def get_intelligence(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> IntelligenceResponse:
    client = ClientService(db).get_client(user, client_id)
    return IntelligenceService(db).get_current(client)


@router.get(
    "/{client_id}/intelligence/status",
    response_model=IntelligenceStatus,
    summary="Intelligence build status",
)
def get_status(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> IntelligenceStatus:
    client = ClientService(db).get_client(user, client_id)
    return IntelligenceService(db).status(client)


@router.get(
    "/{client_id}/intelligence/versions",
    response_model=list[ProfileVersionItem],
    summary="Profile version history",
)
def list_versions(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[ProfileVersionItem]:
    client = ClientService(db).get_client(user, client_id)
    return IntelligenceService(db).versions(client)


@router.get(
    "/{client_id}/intelligence/versions/{version}",
    response_model=IntelligenceResponse,
    summary="A specific profile version",
)
def get_version(
    client_id: uuid.UUID, version: int, user: CurrentUser, db: DbSession
) -> IntelligenceResponse:
    client = ClientService(db).get_client(user, client_id)
    return IntelligenceService(db).get_version(client, version)


@router.post(
    "/{client_id}/intelligence/rebuild",
    response_model=IntelligenceStatus,
    summary="Force a full intelligence rebuild (admin)",
)
def rebuild(
    client_id: uuid.UUID, admin: AdminUser, db: DbSession
) -> IntelligenceStatus:
    client = ClientService(db).get_client(admin, client_id)
    return IntelligenceService(db).rebuild(client)


@router.post(
    "/{client_id}/directives/{directive_id}/resolve",
    response_model=DirectiveRead,
    summary="Resolve a conflicted directive (admin)",
)
def resolve_directive(
    client_id: uuid.UUID,
    directive_id: uuid.UUID,
    admin: AdminUser,
    db: DbSession,
    activate: bool = Query(True, description="Keep active (true) or dismiss (false)"),
) -> DirectiveRead:
    client = ClientService(db).get_client(admin, client_id)
    return IntelligenceService(db).resolve_directive(client, directive_id, activate)


@router.get(
    "/{client_id}/context",
    response_model=ClientContextResponse,
    summary="Debug: the context every agent receives for this client",
)
def get_context(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    query: str | None = Query(None, description="Optional query for RAG retrieval"),
) -> ClientContextResponse:
    ClientService(db).get_client(user, client_id)  # access scoping
    ctx = ContextService(db, get_embedder()).build(client_id, query=query)
    return ClientContextResponse(
        version=ctx.version,
        preamble=ctx.preamble,
        capability_flags=ctx.capability_flags,
        directives=[DirectiveRead.model_validate(d) for d in ctx.directives],
        retrieved=[
            RetrievedChunk(
                text=chunk.text,
                source_label=(chunk.meta or {}).get("label"),
                score=round(score, 4),
            )
            for chunk, score in ctx.retrieved
        ],
    )
