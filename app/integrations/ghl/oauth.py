"""GoHighLevel (LeadConnector) OAuth2 — real Marketplace App authorization.

A real authorization-code redirect through our own app, same shape as
``app/integrations/google/oauth.py``/``meta/oauth.py``: GHL's Marketplace App
model (https://marketplace.gohighlevel.com/docs/Authorization/OAuth2.0/) uses
the standard ``response_type=code`` → redirect → ``grant_type=authorization_code``
exchange. One difference from Google/Meta: this is deliberately performed
ONCE, agency-wide (see ``app.models.ghl_agency_connection.GhlAgencyConnection``),
not once per client — this engagement's GHL setup is one shared location for
every client, so there is only ever one grant to hold.

``user_type=Location`` on both the token exchange and the refresh call
because the authorize URL used here (``/oauth/chooselocation``) has the
installing user pick the one shared location during GHL's own consent screen
— the resulting token pair is already location-scoped, with no separate
"mint a location token from an agency token" step needed (see GHL's
``POST /oauth/locationToken`` — only relevant for a true multi-location agency
install, which this engagement is not).
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ProviderAuthError, ServiceUnavailableError

_TIMEOUT = 20.0


class GhlOAuthClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    @property
    def is_configured(self) -> bool:
        return self._s.ghl_configured

    def _require(self) -> None:
        if not self.is_configured:
            raise ServiceUnavailableError(
                "GHL integration is not configured on this server "
                "(GHL_CLIENT_ID / GHL_CLIENT_SECRET / GHL_REDIRECT_URI)."
            )

    def authorization_url(self, state: str) -> str:
        self._require()
        query = urlencode(
            {
                "response_type": "code",
                "redirect_uri": self._s.ghl_redirect_uri,
                "client_id": self._s.ghl_client_id,
                "scope": self._s.ghl_scopes,
                "state": state,
            }
        )
        return f"{self._s.ghl_authorize_url}?{query}"

    async def exchange_code(self, code: str) -> dict:
        """Authorization code → {access_token, refresh_token, expires_in,
        locationId, companyId}."""
        self._require()
        return await self._post(
            {
                "code": code,
                "client_id": self._s.ghl_client_id,
                "client_secret": self._s.ghl_client_secret,
                "grant_type": "authorization_code",
                "user_type": "Location",
                "redirect_uri": self._s.ghl_redirect_uri,
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh token → a fresh {access_token, refresh_token, expires_in}.

        GHL rotates the refresh token on every use (unlike Google) — callers
        must persist the ``refresh_token`` this returns, not reuse the old one.
        """
        self._require()
        return await self._post(
            {
                "client_id": self._s.ghl_client_id,
                "client_secret": self._s.ghl_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "user_type": "Location",
            }
        )

    async def _post(self, data: dict) -> dict:
        token_url = f"{self._s.ghl_base_url}/oauth/token"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.post(token_url, data=data)
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach GHL: {exc}", code="ghl_unreachable", status_code=502
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload:
            message = payload.get("error_description") or payload.get("error") or resp.text[:200]
            # A rejected code/refresh means the grant itself is dead (revoked,
            # already-rotated refresh token reused, etc.) — always a
            # reconnect, never a transient failure worth retrying as-is.
            raise ProviderAuthError(f"GHL rejected the token request: {message}")
        return payload


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
