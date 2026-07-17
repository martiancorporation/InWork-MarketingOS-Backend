"""Meta (Facebook/Instagram) OAuth2 — authorization-code flow, per client.

Our platform registers ONE Meta app (``META_APP_ID`` / ``META_APP_SECRET`` /
``META_REDIRECT_URI`` in config); each marketing client authorizes it, yielding
that client's own long-lived token which we store encrypted on their
``integrations`` row. This client only talks to Meta's fixed OAuth/Graph hosts.

Graceful degradation: when the app isn't configured, ``is_configured`` is False
and callers surface a 503 rather than attempting a call.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ServiceUnavailableError

_DIALOG = "https://www.facebook.com/{version}/dialog/oauth"
_GRAPH = "https://graph.facebook.com/{version}"
_TIMEOUT = 20.0


class MetaOAuthClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    @property
    def is_configured(self) -> bool:
        return self._s.meta_configured

    def _require(self) -> None:
        if not self.is_configured:
            raise ServiceUnavailableError(
                "Meta integration is not configured on this server "
                "(META_APP_ID / META_APP_SECRET / META_REDIRECT_URI)."
            )

    def authorization_url(self, state: str) -> str:
        """Build the consent URL the operator sends the client to."""
        self._require()
        query = urlencode(
            {
                "client_id": self._s.meta_app_id,
                "redirect_uri": self._s.meta_redirect_uri,
                "state": state,
                "scope": self._s.meta_scopes,
                "response_type": "code",
            }
        )
        return f"{_DIALOG.format(version=self._s.meta_api_version)}?{query}"

    async def exchange_code(self, code: str) -> dict:
        """Authorization code → short-lived access token."""
        self._require()
        return await self._get(
            "/oauth/access_token",
            {
                "client_id": self._s.meta_app_id,
                "client_secret": self._s.meta_app_secret,
                "redirect_uri": self._s.meta_redirect_uri,
                "code": code,
            },
        )

    async def exchange_long_lived(self, short_token: str) -> dict:
        """Short-lived token → long-lived (~60 day) token."""
        self._require()
        return await self._get(
            "/oauth/access_token",
            {
                "grant_type": "fb_exchange_token",
                "client_id": self._s.meta_app_id,
                "client_secret": self._s.meta_app_secret,
                "fb_exchange_token": short_token,
            },
        )

    async def list_ad_accounts(self, access_token: str) -> list[dict]:
        """The ad accounts this token can read (id + name)."""
        self._require()
        data = await self._get(
            "/me/adaccounts",
            {"fields": "account_id,name", "access_token": access_token},
        )
        return list(data.get("data") or [])

    async def _get(self, path: str, params: dict) -> dict:
        url = f"{_GRAPH.format(version=self._s.meta_api_version)}{path}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach Meta: {exc}", code="meta_unreachable", status_code=502
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload:
            message = (payload.get("error") or {}).get("message") or resp.text[:200]
            raise AppError(
                f"Meta rejected the request: {message}",
                code="meta_oauth_error",
                status_code=400,
            )
        return payload


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
