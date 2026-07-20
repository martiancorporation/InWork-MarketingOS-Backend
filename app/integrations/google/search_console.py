"""Google Search Console (Search Analytics API) — read organic-search performance.

Authenticated by the client's OAuth access token (the shared Google OAuth client,
scope ``webmasters.readonly``). ``list_sites`` discovers the verified properties a
token can read so completion can bind one; ``fetch_metrics`` queries the last 30
days of search analytics and normalizes it into the flat analytics shape
(impressions + clicks map directly; Search Console has no spend/revenue). This
data lands in ``analytics_daily`` under the ``seo`` platform bucket.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_BASE = "https://searchconsole.googleapis.com/webmasters/v3"
_TIMEOUT = 30.0


class SearchConsoleClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    async def list_sites(self, access_token: str) -> list[str]:
        """Verified site URLs this token can read, e.g. ['https://acme.com/']."""
        data = await self._request("GET", f"{_BASE}/sites", access_token)
        return [
            entry.get("siteUrl") for entry in (data.get("siteEntry") or []) if entry.get("siteUrl")
        ]

    async def fetch_metrics(self, access_token: str, site_url: str) -> dict:
        today = date.today()
        start = today - timedelta(days=30)
        url = f"{_BASE}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
        body = {
            "startDate": start.isoformat(),
            "endDate": today.isoformat(),
            "dimensions": [],  # totals only
            "rowLimit": 1,
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
                f"Could not reach Search Console: {exc}",
                code="search_console_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or "error" in payload:
            message = (payload.get("error") or {}).get("message") or resp.text[:200]
            raise AppError(
                f"Search Console rejected the request: {message}",
                code="search_console_error",
                status_code=400,
            )
        return payload


def _normalize(payload: dict) -> dict:
    """Aggregate query rows → flat AnalyticsDailyIn-shaped totals."""
    impressions = clicks = 0
    for row in payload.get("rows") or []:
        impressions += int(float(row.get("impressions", 0) or 0))
        clicks += int(float(row.get("clicks", 0) or 0))
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": 0.0,
        "conversions": 0,
        "leads": 0,
        "revenue": 0.0,
    }


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
