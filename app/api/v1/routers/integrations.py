"""Per-client integrations API (v1) — connect / disconnect the connector catalog.

- ``GET  /clients/{id}/integrations``                     — full connector catalog
- ``GET  /clients/{id}/integrations/{key}``               — one connector's state
- ``POST /clients/{id}/integrations/{key}/oauth/start``   — begin real OAuth (Meta)
- ``POST /clients/{id}/integrations/{key}/oauth/complete`` — finish OAuth, store token
- ``POST /clients/{id}/integrations/{key}/sync``          — pull live insights
- ``POST /clients/{id}/integrations/{key}/connect``       — placeholder connect (other providers)
- ``POST /clients/{id}/integrations/{key}/disconnect``    — reset to disconnected

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404, never revealing its
existence. Any user who can see the client may manage its integrations.

**Meta** runs the real per-client OAuth2 flow (start → complete → sync); tokens
are stored encrypted. Other providers still use ``connect`` until their client
is built. See ``IntegrationService``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_capability
from app.models.client import Client
from app.models.enums import ClientCapability, IntegrationKey
from app.schemas.integration import (
    IntegrationConnectRequest,
    IntegrationListResponse,
    IntegrationRead,
    OAuthCompleteRequest,
    OAuthStartResponse,
)
from app.services.client_service import ClientService
from app.services.integration_service import IntegrationService

router = APIRouter(prefix="/clients/{client_id}/integrations", tags=["integrations"])


@router.get("", response_model=IntegrationListResponse, summary="List integrations")
def list_integrations(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> IntegrationListResponse:
    ClientService(db).get_client(user, client_id)  # 404 if not accessible
    return IntegrationService(db).list(client_id)


@router.get(
    "/{key}", response_model=IntegrationRead, summary="Get one integration"
)
def get_integration(
    client_id: uuid.UUID, key: IntegrationKey, user: CurrentUser, db: DbSession
) -> IntegrationRead:
    ClientService(db).get_client(user, client_id)
    integration = IntegrationService(db).get(client_id, key)
    return IntegrationRead.model_validate(integration)


@router.post(
    "/{key}/oauth/start",
    response_model=OAuthStartResponse,
    summary="Begin real OAuth (Meta) — returns the authorization URL",
)
def oauth_start(
    client_id: uuid.UUID, key: IntegrationKey, user: CurrentUser, db: DbSession
) -> OAuthStartResponse:
    ClientService(db).get_client(user, client_id)
    url, state = IntegrationService(db).oauth_start(client_id, key)
    return OAuthStartResponse(authorization_url=url, state=state)


@router.post(
    "/{key}/oauth/complete",
    response_model=IntegrationRead,
    summary="Finish OAuth — exchange the code, store the client's token (encrypted)",
)
async def oauth_complete(
    client_id: uuid.UUID,
    key: IntegrationKey,
    data: OAuthCompleteRequest,
    user: CurrentUser,
    db: DbSession,
) -> IntegrationRead:
    ClientService(db).get_client(user, client_id)
    integration = await IntegrationService(db).oauth_complete(
        client_id, key, data.code, data.state, ad_account_id=data.ad_account_id
    )
    return IntegrationRead.model_validate(integration)


@router.post(
    "/{key}/sync",
    response_model=IntegrationRead,
    summary="Pull live insights from the provider into analytics",
)
async def sync_integration(
    client_id: uuid.UUID, key: IntegrationKey, user: CurrentUser, db: DbSession
) -> IntegrationRead:
    ClientService(db).get_client(user, client_id)
    integration = await IntegrationService(db).sync(client_id, key)
    return IntegrationRead.model_validate(integration)


@router.post(
    "/{key}/connect",
    response_model=IntegrationRead,
    summary="Connect an integration (placeholder — non-Meta providers)",
)
def connect_integration(
    client_id: uuid.UUID,
    key: IntegrationKey,
    data: IntegrationConnectRequest,
    db: DbSession,
    # Requires the ``manage_integrations`` capability (admins/managers always pass;
    # 404 if inaccessible, 403 if accessible-but-unauthorized).
    _client: Annotated[
        Client, Depends(require_capability(ClientCapability.manage_integrations))
    ],
) -> IntegrationRead:
    integration = IntegrationService(db).connect(client_id, key, data)
    return IntegrationRead.model_validate(integration)


@router.post(
    "/{key}/disconnect",
    response_model=IntegrationRead,
    summary="Disconnect an integration",
)
def disconnect_integration(
    client_id: uuid.UUID, key: IntegrationKey, user: CurrentUser, db: DbSession
) -> IntegrationRead:
    ClientService(db).get_client(user, client_id)
    integration = IntegrationService(db).disconnect(client_id, key)
    return IntegrationRead.model_validate(integration)
