"""LinkedIn Marketing API — read ad-account analytics.

Given a client's stored access token, ``list_ad_accounts`` discovers the ad
accounts it can manage (so completion can bind one) and ``fetch_metrics`` pulls
the last 30 days of account analytics, normalized into the flat shape our
analytics layer expects (impressions/clicks/spend/leads/conversions/revenue).
All calls send the versioned-API headers LinkedIn requires.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_REST = "https://api.linkedin.com/rest"
_TIMEOUT = 30.0


class LinkedInClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    def _headers(self, access_token: str) -> dict:
        return {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": self._s.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def list_ad_accounts(self, access_token: str) -> list[dict]:
        """Ad accounts this token can manage (id + name)."""
        data = await self._request(
            "GET", f"{_REST}/adAccounts?q=search", access_token
        )
        accounts: list[dict] = []
        for el in data.get("elements") or []:
            acc_id = el.get("id")
            if acc_id is not None:
                accounts.append({"id": str(acc_id), "name": el.get("name")})
        return accounts

    async def fetch_metrics(self, access_token: str, account_id: str) -> dict:
        """Last-30-day account analytics for the bound ad account."""
        acc = (account_id or "").split(":")[-1]  # accept "urn:li:sponsoredAccount:123"
        today = date.today()
        start = today - timedelta(days=30)
        params = {
            "q": "analytics",
            "pivot": "ACCOUNT",
            "timeGranularity": "ALL",
            "accounts[0]": f"urn:li:sponsoredAccount:{acc}",
            "dateRange.start.day": start.day,
            "dateRange.start.month": start.month,
            "dateRange.start.year": start.year,
            "dateRange.end.day": today.day,
            "dateRange.end.month": today.month,
            "dateRange.end.year": today.year,
            "fields": (
                "impressions,clicks,costInUsd,externalWebsiteConversions,oneClickLeads"
            ),
        }
        data = await self._request(
            "GET", f"{_REST}/adAnalytics", access_token, params=params
        )
        return _normalize(data)

    async def _request(
        self, method: str, url: str, access_token: str, params: dict | None = None
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.request(
                    method, url, headers=self._headers(access_token), params=params
                )
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach LinkedIn: {exc}",
                code="linkedin_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload or "serviceErrorCode" in payload:
            message = payload.get("message") or resp.text[:200]
            raise AppError(
                f"LinkedIn rejected the request: {message}",
                code="linkedin_error",
                status_code=400,
            )
        return payload


def _normalize(payload: dict) -> dict:
    """Aggregate adAnalytics elements → flat AnalyticsDailyIn-shaped totals."""
    impressions = clicks = 0
    spend = 0.0
    conversions = 0
    leads = 0
    for el in payload.get("elements") or []:
        impressions += int(float(el.get("impressions", 0) or 0))
        clicks += int(float(el.get("clicks", 0) or 0))
        spend += float(el.get("costInUsd", 0) or 0)
        conversions += int(float(el.get("externalWebsiteConversions", 0) or 0))
        leads += int(float(el.get("oneClickLeads", 0) or 0))
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": round(spend, 2),
        "conversions": conversions,
        "leads": leads,
        "revenue": 0.0,
    }


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
