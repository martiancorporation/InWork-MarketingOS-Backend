"""Google OAuth2 — shared authorization-code + refresh flow for all Google APIs.

One Google Cloud OAuth client (``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET`` /
``GOOGLE_REDIRECT_URI``) serves every Google integration (Google Ads today, GA4 /
Search Console next); the per-integration difference is only the ``scope``.
Unlike Meta, Google issues a **refresh token** (with ``access_type=offline`` +
``prompt=consent``), so callers can refresh an expired access token without a new
user consent — see ``refresh_access_token``.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ServiceUnavailableError

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint URL, not a secret
_TIMEOUT = 20.0


class GoogleOAuthClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    @property
    def is_configured(self) -> bool:
        return self._s.google_configured

    def _require(self) -> None:
        if not self.is_configured:
            raise ServiceUnavailableError(
                "Google integration is not configured on this server "
                "(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI)."
            )

    def authorization_url(self, state: str, scope: str) -> str:
        self._require()
        query = urlencode(
            {
                "client_id": self._s.google_client_id,
                "redirect_uri": self._s.google_redirect_uri,
                "response_type": "code",
                "scope": scope,
                "state": state,
                "access_type": "offline",  # ask for a refresh token
                "prompt": "consent",  # force a refresh token even on re-auth
                "include_granted_scopes": "true",
            }
        )
        return f"{_AUTH}?{query}"

    async def exchange_code(self, code: str) -> dict:
        """Authorization code → {access_token, refresh_token, expires_in}."""
        self._require()
        return await self._post(
            {
                "code": code,
                "client_id": self._s.google_client_id,
                "client_secret": self._s.google_client_secret,
                "redirect_uri": self._s.google_redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh token → a fresh {access_token, expires_in} (no new consent)."""
        self._require()
        return await self._post(
            {
                "refresh_token": refresh_token,
                "client_id": self._s.google_client_id,
                "client_secret": self._s.google_client_secret,
                "grant_type": "refresh_token",
            }
        )

    async def _post(self, data: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.post(_TOKEN, data=data)
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach Google: {exc}", code="google_unreachable", status_code=502
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload:
            message = (
                payload.get("error_description") or payload.get("error") or resp.text[:200]
            )
            raise AppError(
                f"Google rejected the token request: {message}",
                code="google_oauth_error",
                status_code=400,
            )
        return payload


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
