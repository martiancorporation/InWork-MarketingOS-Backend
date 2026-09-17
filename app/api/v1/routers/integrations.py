"""Per-client integrations API (v1) — connect / disconnect the connector catalog.

- ``GET  /clients/{id}/integrations``                     — full connector catalog
- ``GET  /clients/{id}/integrations/{key}``               — one connector's state
- ``POST /clients/{id}/integrations/{key}/oauth/start``   — begin real OAuth (Meta, Google family)
- ``POST /clients/{id}/integrations/{key}/oauth/complete`` — finish OAuth, store token
- ``POST /clients/{id}/integrations/{key}/oauth/select-account`` — pick an ad account/property/site
  when ``oauth/complete`` came back ambiguous (``available_accounts`` non-empty)
- ``POST /clients/{id}/integrations/{key}/sync``          — pull live insights
- ``POST /clients/{id}/integrations/{key}/connect``       — placeholder connect (other providers)
- ``POST /clients/{id}/integrations/{key}/disconnect``    — reset to disconnected

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404, never revealing its
existence. Any user who can see the client may manage its integrations.

**Meta and the Google family** (Ads/LSA/GA4/Search Console) run the real
per-client OAuth2 flow (start → complete → [select-account] → sync); tokens
are stored encrypted, and the bound account is never auto-picked — the
operator always confirms it, even when only one is found (see
``IntegrationService._select_ad_account``/``_select_google_account``). Other
providers still use ``connect`` until their client is built.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, RequireClient, require_capability
from app.core.exceptions import BadRequestError
from app.models.client import Client
from app.models.enums import ClientCapability, IntegrationKey
from app.schemas.integration import (
    AdAccountOption,
    GhlConnectRequest,
    GhlContactRead,
    GhlContactsRead,
    IntegrationConnectRequest,
    IntegrationListResponse,
    IntegrationRead,
    OAuthCompleteRequest,
    OAuthStartResponse,
    SelectAccountRequest,
)
from app.services.integration_service import IntegrationService

router = APIRouter(prefix="/clients/{client_id}/integrations", tags=["integrations"])


def _encode_cursor(cursor: list | None) -> str | None:
    if not cursor:
        return None
    return base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode()


def _decode_cursor(raw: str | None) -> list | None:
    if not raw:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except Exception as exc:
        raise BadRequestError("Invalid pagination cursor.") from exc


# ---- GHL — registered ahead of the generic "/{key}" routes below, since a
# literal "/ghl/..." path would otherwise be shadowed by "/{key}/..." (route
# matching is order-dependent, not specificity-dependent). GHL never runs
# oauth/start|complete — see IntegrationService.connect_ghl for why. ------- #


@router.post(
    "/ghl/connect",
    response_model=IntegrationRead,
    summary="Connect GHL with a token issued out-of-band by the client's team",
)
def connect_ghl(
    client_id: uuid.UUID,
    data: GhlConnectRequest,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_integrations))],
) -> IntegrationRead:
    integration = IntegrationService(db).connect_ghl(
        client_id,
        access_token=data.access_token,
        refresh_token=data.refresh_token,
        location_id=data.location_id,
        tags=data.tags,
        expires_in=data.expires_in,
    )
    return IntegrationRead.model_validate(integration)


@router.get(
    "/ghl/contacts",
    response_model=GhlContactsRead,
    summary="Fetch this client's tagged GHL contacts (one page)",
)
async def get_ghl_contacts(
    client_id: uuid.UUID,
    db: DbSession,
    _client: RequireClient,
    page_limit: Annotated[int, Query(ge=1, le=100)] = 100,
    search_after: Annotated[
        str | None,
        Query(description="Opaque cursor from a previous response's next_search_after"),
    ] = None,
) -> GhlContactsRead:
    page = await IntegrationService(db).fetch_ghl_contacts(
        client_id, page_limit=page_limit, search_after=_decode_cursor(search_after)
    )
    return GhlContactsRead(
        contacts=[GhlContactRead.model_validate(c) for c in page.contacts],
        next_search_after=_encode_cursor(page.next_search_after),
    )


@router.get("", response_model=IntegrationListResponse, summary="List integrations")
def list_integrations(
    client_id: uuid.UUID, db: DbSession, _client: RequireClient
) -> IntegrationListResponse:
    return IntegrationService(db).list(client_id)


@router.get("/{key}", response_model=IntegrationRead, summary="Get one integration")
def get_integration(
    client_id: uuid.UUID, key: IntegrationKey, db: DbSession, _client: RequireClient
) -> IntegrationRead:
    integration = IntegrationService(db).get(client_id, key)
    return IntegrationRead.model_validate(integration)


@router.post(
    "/{key}/oauth/start",
    response_model=OAuthStartResponse,
    summary="Begin real OAuth (Meta) — returns the authorization URL",
)
def oauth_start(
    client_id: uuid.UUID,
    key: IntegrationKey,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_integrations))],
) -> OAuthStartResponse:
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
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_integrations))],
) -> IntegrationRead:
    integration, pending_accounts = await IntegrationService(db).oauth_complete(
        client_id,
        key,
        data.code,
        data.state,
        ad_account_id=data.ad_account_id,
        login_customer_id=data.login_customer_id,
    )
    result = IntegrationRead.model_validate(integration)
    if pending_accounts:
        result.available_accounts = [
            AdAccountOption(id=str(a.get("account_id") or a.get("id")), name=a.get("name"))
            for a in pending_accounts
        ]
    return result


@router.post(
    "/{key}/oauth/select-account",
    response_model=IntegrationRead,
    summary="Pick which ad account/property/site to bind (when oauth/complete came back ambiguous)",
)
async def oauth_select_account(
    client_id: uuid.UUID,
    key: IntegrationKey,
    data: SelectAccountRequest,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_integrations))],
) -> IntegrationRead:
    integration = await IntegrationService(db).select_account(
        client_id, key, data.ad_account_id, login_customer_id=data.login_customer_id
    )
    return IntegrationRead.model_validate(integration)


@router.post(
    "/{key}/sync",
    response_model=IntegrationRead,
    summary="Pull live insights from the provider into analytics",
)
async def sync_integration(
    client_id: uuid.UUID,
    key: IntegrationKey,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_integrations))],
) -> IntegrationRead:
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
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_integrations))],
) -> IntegrationRead:
    integration = IntegrationService(db).connect(client_id, key, data)
    return IntegrationRead.model_validate(integration)


@router.post(
    "/{key}/disconnect",
    response_model=IntegrationRead,
    summary="Disconnect an integration",
)
def disconnect_integration(
    client_id: uuid.UUID,
    key: IntegrationKey,
    db: DbSession,
    _client: Annotated[Client, Depends(require_capability(ClientCapability.manage_integrations))],
) -> IntegrationRead:
    integration = IntegrationService(db).disconnect(client_id, key)
    return IntegrationRead.model_validate(integration)
