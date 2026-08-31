"""Google Analytics 4 (Data API v1) — read website analytics.

Authenticated by the client's OAuth access token (the shared Google OAuth client,
scope ``analytics.readonly``). ``list_properties`` discovers the GA4 properties a
token can read (Admin API) so completion can bind one; ``fetch_daily_insights``
runs a ``runReport`` broken down by day (not one aggregated blob) and normalizes
each day into the same flat shape as the Meta / Google Ads clients so it feeds
``analytics_daily`` identically (GA4 has no ad spend, so ``spend`` is always 0).
"""

from __future__ import annotations

from datetime import date, datetime

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_DATA = "https://analyticsdata.googleapis.com/v1beta"
_ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
_TIMEOUT = 30.0
# Metric order matters — the response rows align to this request order.
_METRICS = ("screenPageViews", "sessions", "conversions", "totalRevenue")

# Breakdown reports (Platform Insights' Analytics Breakdown) — one GA4
# dimension each, ranked by its lead metric, top 10 only. Order matters here
# too — normalization zips these against the response's metricValues.
_BREAKDOWN_METRICS = ("sessions", "screenPageViews", "conversions", "totalRevenue")
_BREAKDOWNS = {
    "top_page": "pagePath",
    "channel": "sessionDefaultChannelGroup",
    "device": "deviceCategory",
}
_BREAKDOWN_LIMIT = 10


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

    async def fetch_daily_insights(
        self, access_token: str, property_id: str, *, days: int = 90
    ) -> list[dict]:
        """One row per day over the last ``days`` days — the historical trend a
        dashboard chart needs, not a single rolled-up total."""
        pid = (property_id or "").removeprefix("properties/")
        url = f"{_DATA}/properties/{pid}:runReport"
        body = {
            "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
            "dimensions": [{"name": "date"}],
            "metrics": [{"name": m} for m in _METRICS],
            "orderBys": [{"dimension": {"dimensionName": "date"}}],
            "limit": 100000,
        }
        data = await self._request("POST", url, access_token, json=body)
        return [_normalize(row) for row in (data.get("rows") or [])]

    async def fetch_breakdowns(
        self, access_token: str, property_id: str, *, days: int = 90
    ) -> dict[str, list[dict]]:
        """Top pages, traffic channels, and device categories over the last
        ``days`` days — a dimensional slice, not a daily series (see
        ``app/models/analytics_breakdown.py`` for why this is a separate
        concept from the daily trend above)."""
        pid = (property_id or "").removeprefix("properties/")
        url = f"{_DATA}/properties/{pid}:runReport"
        out: dict[str, list[dict]] = {}
        for breakdown_type, dimension in _BREAKDOWNS.items():
            body = {
                "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
                "dimensions": [{"name": dimension}],
                "metrics": [{"name": m} for m in _BREAKDOWN_METRICS],
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                "limit": _BREAKDOWN_LIMIT,
            }
            data = await self._request("POST", url, access_token, json=body)
            out[breakdown_type] = [
                _normalize_breakdown_row(row) for row in (data.get("rows") or [])
            ]
        return out

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


def _normalize(row: dict) -> dict:
    """One ``runReport`` row (with a ``date`` dimension) → flat
    AnalyticsDailyIn-shaped totals, keyed by ``row["dimensionValues"][0]``
    (a ``YYYYMMDD`` string)."""
    dim_values = row.get("dimensionValues") or []
    date_str = dim_values[0].get("value") if dim_values else None
    metric_values = row.get("metricValues") or []
    values = [v.get("value", 0) for v in metric_values]
    by_metric = dict(zip(_METRICS, values))
    conversions = int(float(by_metric.get("conversions", 0) or 0))
    return {
        "date": datetime.strptime(date_str, "%Y%m%d").date() if date_str else date.today(),
        "impressions": int(float(by_metric.get("screenPageViews", 0) or 0)),
        "clicks": int(float(by_metric.get("sessions", 0) or 0)),
        "spend": 0.0,  # GA4 has no ad spend
        "conversions": conversions,
        "leads": conversions,
        "revenue": round(float(by_metric.get("totalRevenue", 0) or 0), 2),
    }


def _normalize_breakdown_row(row: dict) -> dict:
    """One breakdown row -> ``{"dimension", "metrics"}`` (rank comes from
    response order, assigned by the caller)."""
    dim_values = row.get("dimensionValues") or []
    dimension = (dim_values[0].get("value") if dim_values else None) or "(not set)"
    metric_values = row.get("metricValues") or []
    values = [v.get("value", 0) for v in metric_values]
    by_metric = dict(zip(_BREAKDOWN_METRICS, values))
    return {
        "dimension": dimension,
        "metrics": {
            "sessions": int(float(by_metric.get("sessions", 0) or 0)),
            "page_views": int(float(by_metric.get("screenPageViews", 0) or 0)),
            "conversions": int(float(by_metric.get("conversions", 0) or 0)),
            "revenue": round(float(by_metric.get("totalRevenue", 0) or 0), 2),
        },
    }


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
