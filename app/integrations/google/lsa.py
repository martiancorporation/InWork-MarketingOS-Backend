"""Google Local Services Ads (Local Services API) — read LSA account performance.

Authenticated by the client's OAuth access token (shared Google OAuth client,
scope ``adwords``) plus our Google-approved developer token, like Google Ads.
``fetch_metrics`` pulls the last 30 days of account reports and normalizes lead
counts + charged spend into the flat analytics shape. LSA account ids are the
Google Ads customer ids the token can access (``list_accessible_customers``),
so completion reuses the Ads discovery path. Data lands in ``analytics_daily``
under the ``google_lsa`` platform bucket.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_BASE = "https://localservices.googleapis.com/v1"
_ADS = "https://googleads.googleapis.com/{version}"
_TIMEOUT = 30.0


class LsaClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    def _headers(self, access_token: str) -> dict:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": self._s.google_developer_token or "",
        }
        if self._s.google_login_customer_id:
            headers["login-customer-id"] = self._s.google_login_customer_id
        return headers

    async def list_accessible_customers(self, access_token: str) -> list[str]:
        """LSA accounts are Google Ads customer ids the token can access."""
        url = (
            f"{_ADS.format(version=self._s.google_ads_api_version)}"
            "/customers:listAccessibleCustomers"
        )
        data = await self._request("GET", url, access_token)
        return [rn.split("/")[-1] for rn in (data.get("resourceNames") or [])]

    async def fetch_metrics(self, access_token: str, customer_id: str) -> dict:
        cid = (customer_id or "").replace("-", "")
        today = date.today()
        start = today - timedelta(days=30)
        params = {
            "query": f"manager_customer_id:{cid}",
            "startDate.year": start.year,
            "startDate.month": start.month,
            "startDate.day": start.day,
            "endDate.year": today.year,
            "endDate.month": today.month,
            "endDate.day": today.day,
        }
        data = await self._request(
            "GET", f"{_BASE}/accountReports:search", access_token, params=params
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
                f"Could not reach Google Local Services: {exc}",
                code="lsa_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload:
            message = (payload.get("error") or {}).get("message") or resp.text[:200]
            raise AppError(
                f"Google Local Services rejected the request: {message}",
                code="lsa_error",
                status_code=400,
            )
        return payload


def _normalize(payload: dict) -> dict:
    """Aggregate accountReports rows → flat AnalyticsDailyIn-shaped totals.

    LSA reports lead counts and charged spend (micros); it has no
    impression/click concept, so those stay 0.
    """
    leads = 0
    charged_micros = 0
    for report in payload.get("accountReports") or []:
        leads += int(float(report.get("totalLeads", 0) or 0))
        charged_micros += int(report.get("totalChargedAmountMicros", 0) or 0)
    return {
        "impressions": 0,
        "clicks": 0,
        "spend": round(charged_micros / 1_000_000, 2),
        "conversions": leads,
        "leads": leads,
        "revenue": 0.0,
    }


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
