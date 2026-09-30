"""Admin GHL (GoHighLevel) agency-connection API — the real, self-serve OAuth2
connect flow for the ONE shared GHL location this engagement's clients are
all scoped into (see ``app.models.ghl_agency_connection.GhlAgencyConnection``).

- ``GET  /admin/ghl``                — the one connection's current status
- ``POST /admin/ghl/oauth/start``    — begin the authorization-code flow
- ``POST /admin/ghl/oauth/complete`` — finish it, store the shared token pair
- ``POST /admin/ghl/disconnect``     — clear the stored token pair

Admin-only: this is an agency-wide setting, not scoped to any one client (a
client's own GHL settings — its tag list — stay on the per-client
``/clients/{id}/integrations/ghl`` routes).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminUser, DbSession
from app.models.enums import IntegrationStatus
from app.schemas.integration import (
    GhlAgencyConnectionRead,
    OAuthCompleteRequest,
    OAuthStartResponse,
)
from app.services.integration_service import IntegrationService

router = APIRouter(prefix="/admin/ghl", tags=["ghl-agency"])


@router.get("", response_model=GhlAgencyConnectionRead, summary="GHL connection status (admin)")
def get_status(admin: AdminUser, db: DbSession) -> GhlAgencyConnectionRead:
    connection = IntegrationService(db).ghl_agency_status()
    if connection is None:
        # No connect attempt yet — a real "disconnected" object, never a bare
        # null body (see GhlAgencyConnectionRead's docstring).
        return GhlAgencyConnectionRead(status=IntegrationStatus.disconnected)
    return GhlAgencyConnectionRead.model_validate(connection)


@router.post(
    "/oauth/start",
    response_model=OAuthStartResponse,
    summary="Begin the GHL OAuth2 connect flow (admin)",
)
def oauth_start(admin: AdminUser, db: DbSession) -> OAuthStartResponse:
    url, state = IntegrationService(db).ghl_oauth_start()
    return OAuthStartResponse(authorization_url=url, state=state)


@router.post(
    "/oauth/complete",
    response_model=GhlAgencyConnectionRead,
    summary="Finish the GHL OAuth2 connect flow (admin)",
)
async def oauth_complete(
    data: OAuthCompleteRequest, admin: AdminUser, db: DbSession
) -> GhlAgencyConnectionRead:
    connection = await IntegrationService(db).ghl_oauth_complete(
        data.code, data.state, actor_user_id=admin.id
    )
    return GhlAgencyConnectionRead.model_validate(connection)


@router.post(
    "/disconnect", response_model=GhlAgencyConnectionRead, summary="Disconnect GHL (admin)"
)
def disconnect(admin: AdminUser, db: DbSession) -> GhlAgencyConnectionRead:
    connection = IntegrationService(db).ghl_disconnect()
    return GhlAgencyConnectionRead.model_validate(connection)
