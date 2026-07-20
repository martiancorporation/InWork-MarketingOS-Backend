"""Google Analytics 4 (Data API v1) — read website analytics.

Authenticated by the client's OAuth access token (the shared Google OAuth client,
scope ``analytics.readonly``). ``list_properties`` discovers the GA4 properties a
token can read (Admin API) so completion can bind one; ``fetch_metrics`` runs a
``runReport`` over the last 30 days and normalizes the result into the same flat
shape as the Meta / Google Ads clients so it feeds ``analytics_daily`` identically
(GA4 has no ad spend, so ``spend`` is always 0).
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_DATA = "https://analyticsdata.googleapis.com/v1beta"
_ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
_TIMEOUT = 30.0
# Metric order matters — the response rows align to this request order.
_METRICS = ("screenPageViews", "sessions", "conversions", "totalRevenue")


class Ga4Client:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    async def list_properties(self, access_token: str) -> list[str]:
        """GA4 property ids (digits) this token can read, e.g. ['123456789']."""
        data = await self._request("GET", f"{_ADMIN}/accountSummaries", access_token)
        ids: list[str] = []
        for summary in data.get("accountSummaries") or []:
            for prop in summary.get("propertySummaries") or []:
                name = prop.get("property") or ""  # "properties/123456789"
                if name:
                    ids.append(name.split("/")[-1])
        return ids

    async def fetch_metrics(self, access_token: str, property_id: str) -> dict:
        pid = (property_id or "").removeprefix("properties/")
        url = f"{_DATA}/properties/{pid}:runReport"
        body = {
            "dateRanges": [{"startDate": "30daysAgo", "endDate": "today"}],
            "metrics": [{"name": m} for m in _METRICS],
        }
        data = await self._request("POST", url, access_token, json=body)
        return _normalize(data)

    async def _request(
        self, method: str, url: str, access_token: str, json: dict | None = None
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach Google Analytics: {exc}",
                code="ga4_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload:
            message = (payload.get("error") or {}).get("message") or resp.text[:200]
            raise AppError(
                f"Google Analytics rejected the request: {message}",
                code="ga4_error",
                status_code=400,
            )
        return payload


def _normalize(payload: dict) -> dict:
    """First (only) report row → flat AnalyticsDailyIn-shaped totals."""
    rows = payload.get("rows") or []
    metric_values = (rows[0].get("metricValues") if rows else []) or []
    values = [v.get("value", 0) for v in metric_values]
    by_metric = dict(zip(_METRICS, values))
    conversions = int(float(by_metric.get("conversions", 0) or 0))
    return {
        "impressions": int(float(by_metric.get("screenPageViews", 0) or 0)),
        "clicks": int(float(by_metric.get("sessions", 0) or 0)),
        "spend": 0.0,  # GA4 has no ad spend
        "conversions": conversions,
        "leads": conversions,
        "revenue": round(float(by_metric.get("totalRevenue", 0) or 0), 2),
    }


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
