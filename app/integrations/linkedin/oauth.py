"""LinkedIn OAuth2 — authorization-code flow, per client.

Our platform registers ONE LinkedIn app (``LINKEDIN_CLIENT_ID`` /
``LINKEDIN_CLIENT_SECRET`` / ``LINKEDIN_REDIRECT_URI``); each marketing client
authorizes it, yielding that client's own access token (and, for approved apps, a
refresh token) which we store encrypted on their ``integrations`` row.

Graceful degradation: when the app isn't configured, ``is_configured`` is False
and callers surface a 503 rather than attempting a call.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ServiceUnavailableError

_AUTH = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"  # noqa: S105 - endpoint, not a secret
_TIMEOUT = 20.0


class LinkedInOAuthClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    @property
    def is_configured(self) -> bool:
        return self._s.linkedin_configured

    def _require(self) -> None:
        if not self.is_configured:
            raise ServiceUnavailableError(
                "LinkedIn integration is not configured on this server "
                "(LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET / LINKEDIN_REDIRECT_URI)."
            )

    def authorization_url(self, state: str) -> str:
        self._require()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._s.linkedin_client_id,
                "redirect_uri": self._s.linkedin_redirect_uri,
                "state": state,
                "scope": self._s.linkedin_scopes.replace(",", " "),
            }
        )
        return f"{_AUTH}?{query}"

    async def exchange_code(self, code: str) -> dict:
        """Authorization code → {access_token, expires_in, refresh_token?}."""
        self._require()
        return await self._post(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._s.linkedin_client_id,
                "client_secret": self._s.linkedin_client_secret,
                "redirect_uri": self._s.linkedin_redirect_uri,
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh token → a fresh {access_token, expires_in} (approved apps only)."""
        self._require()
        return await self._post(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._s.linkedin_client_id,
                "client_secret": self._s.linkedin_client_secret,
            }
        )

    async def _post(self, data: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.post(
                    _TOKEN,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach LinkedIn: {exc}",
                code="linkedin_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload:
            message = payload.get("error_description") or payload.get("error") or resp.text[:200]
            raise AppError(
                f"LinkedIn rejected the token request: {message}",
                code="linkedin_oauth_error",
                status_code=400,
            )
        return payload


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
