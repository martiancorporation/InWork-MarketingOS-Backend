"""Google Ads API (read-only) — pull account performance metrics.

Uses the REST ``searchStream`` endpoint with a GAQL query, authenticated by the
client's OAuth access token plus our Google-approved ``developer-token`` (and the
manager ``login-customer-id`` header when set). Metrics are normalized into the
same flat shape as the Meta client so both ad platforms feed ``analytics_daily``
identically (cost is reported in micros → dollars).
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_BASE = "https://googleads.googleapis.com/{version}"
_TIMEOUT = 30.0
_GAQL = (
    "SELECT metrics.impressions, metrics.clicks, metrics.cost_micros, "
    "metrics.conversions, metrics.conversions_value "
    "FROM customer WHERE segments.date DURING LAST_30_DAYS"
)


class GoogleAdsClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    def _headers(self, access_token: str) -> dict:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": self._s.google_developer_token or "",
            "Content-Type": "application/json",
        }
        if self._s.google_login_customer_id:
            headers["login-customer-id"] = self._s.google_login_customer_id
        return headers

    async def list_accessible_customers(self, access_token: str) -> list[str]:
        """Customer ids (digits) this token can access, e.g. ['1234567890']."""
        url = f"{_BASE.format(version=self._s.google_ads_api_version)}/customers:listAccessibleCustomers"
        data = await self._request("GET", url, access_token)
        # resourceNames look like "customers/1234567890".
        return [rn.split("/")[-1] for rn in (data.get("resourceNames") or [])]

    async def fetch_metrics(self, access_token: str, customer_id: str) -> dict:
        cid = (customer_id or "").replace("-", "")
        url = (
            f"{_BASE.format(version=self._s.google_ads_api_version)}"
            f"/customers/{cid}/googleAds:searchStream"
        )
        data = await self._request("POST", url, access_token, json={"query": _GAQL})
        return _normalize(data)

    async def _request(
        self, method: str, url: str, access_token: str, json: dict | None = None
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.request(
                    method, url, headers=self._headers(access_token), json=json
                )
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach Google Ads: {exc}",
                code="google_ads_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or (isinstance(payload, dict) and "error" in payload):
            err = payload.get("error") if isinstance(payload, dict) else None
            message = (err or {}).get("message") if isinstance(err, dict) else resp.text[:200]
            raise AppError(
                f"Google Ads rejected the request: {message}",
                code="google_ads_error",
                status_code=400,
            )
        return payload if isinstance(payload, dict) else {"data": payload}


def _normalize(payload: dict) -> dict:
    """Aggregate searchStream results into flat AnalyticsDailyIn-shaped totals.

    ``searchStream`` returns a list of batches, each with ``results[]`` rows
    carrying a ``metrics`` object; we sum across all rows for the period.
    """
    batches = payload.get("data") if isinstance(payload.get("data"), list) else [payload]
    impressions = clicks = 0
    cost_micros = 0
    conversions = 0.0
    conv_value = 0.0
    for batch in batches or []:
        for row in (batch or {}).get("results") or []:
            m = row.get("metrics") or {}
            impressions += int(m.get("impressions", 0) or 0)
            clicks += int(m.get("clicks", 0) or 0)
            cost_micros += int(m.get("costMicros", m.get("cost_micros", 0)) or 0)
            conversions += float(m.get("conversions", 0) or 0)
            conv_value += float(m.get("conversionsValue", m.get("conversions_value", 0)) or 0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": round(cost_micros / 1_000_000, 2),
        "conversions": int(conversions),
        "leads": int(conversions),  # Google Ads "conversions" are the lead/action count
        "revenue": round(conv_value, 2),
    }


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {}
