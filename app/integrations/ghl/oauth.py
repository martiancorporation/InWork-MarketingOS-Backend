"""GoHighLevel (LeadConnector) OAuth2 — refresh-only.

Unlike Meta/Google/LinkedIn, we never run an authorization-code redirect
through our own app for GHL: the client's team issues an access token +
refresh token to us directly, out-of-band, for their single shared location.
The only OAuth step we perform ourselves is refreshing an expired access
token via the standard ``grant_type=refresh_token`` flow.
"""

from __future__ import annotations

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
                "(GHL_CLIENT_ID / GHL_CLIENT_SECRET)."
            )

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh token → a fresh {access_token, refresh_token, expires_in}.

        GHL rotates the refresh token on every use (unlike Google) — callers
        must persist the ``refresh_token`` this returns, not reuse the old one.
        """
        self._require()
        token_url = f"{self._s.ghl_base_url}/oauth/token"
        data = {
            "client_id": self._s.ghl_client_id,
            "client_secret": self._s.ghl_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
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
            # A rejected refresh means the grant itself is dead (revoked,
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
