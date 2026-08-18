"""Meta Marketing API (Graph) — read ad-account insights.

Given a client's stored access token + ad-account id, pulls a **day-by-day**
performance series (not one aggregated blob) and normalizes each day into the
flat shape our analytics/campaign layer expects
(impressions/clicks/spend/leads/conversions/revenue). Lead and conversion
counts come out of the Graph ``actions`` breakdown.
"""

from __future__ import annotations

from datetime import date

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_GRAPH = "https://graph.facebook.com/{version}"
_TIMEOUT = 30.0
# action_types Meta reports that we count as a "lead" or a "conversion".
_LEAD_ACTIONS = {"lead", "leadgen.other", "onsite_conversion.lead_grouped"}
_CONVERSION_ACTIONS = {"purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase"}
_MAX_PAGES = 10  # defensive cap — 10 pages @ up to 100 rows covers ~2.7 years daily


class MetaInsights(dict):
    """Normalized insight row (a plain dict; keys match AnalyticsDailyIn)."""


class MetaClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    async def fetch_daily_insights(
        self, access_token: str, ad_account_id: str, *, date_preset: str = "last_90d"
    ) -> list[MetaInsights]:
        """One row per day over ``date_preset`` (Graph API ``time_increment=1``)
        — the historical trend a dashboard chart needs, not a single rolled-up
        total. Follows pagination so a wide window isn't silently truncated."""
        account = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        url = f"{_GRAPH.format(version=self._s.meta_api_version)}/{account}/insights"
        params = {
            "fields": "impressions,clicks,spend,actions,action_values,date_start",
            "time_increment": 1,
            "date_preset": date_preset,
            "limit": 100,
            "access_token": access_token,
        }
        raw_rows: list[dict] = []
        next_url: str | None = None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            for _ in range(_MAX_PAGES):
                try:
                    resp = (
                        await http.get(next_url) if next_url else await http.get(url, params=params)
                    )
                except httpx.HTTPError as exc:
                    raise AppError(
                        f"Could not reach Meta: {exc}", code="meta_unreachable", status_code=502
                    ) from exc
                payload = _safe_json(resp)
                if resp.status_code >= 400 or "error" in payload:
                    message = (payload.get("error") or {}).get("message") or resp.text[:200]
                    raise AppError(
                        f"Meta insights failed: {message}",
                        code="meta_insights_error",
                        status_code=400,
                    )
                raw_rows.extend(payload.get("data") or [])
                next_url = (payload.get("paging") or {}).get("next")
                if not next_url:
                    break
        return [_normalize(row) for row in raw_rows]


def _sum_actions(actions: list, wanted: set[str]) -> int:
    total = 0
    for a in actions or []:
        if a.get("action_type") in wanted:
            try:
                total += int(float(a.get("value", 0)))
            except (TypeError, ValueError):
                continue
    return total


def _sum_values(action_values: list, wanted: set[str]) -> float:
    total = 0.0
    for a in action_values or []:
        if a.get("action_type") in wanted:
            try:
                total += float(a.get("value", 0))
            except (TypeError, ValueError):
                continue
    return round(total, 2)


def _normalize(row: dict) -> MetaInsights:
    actions = row.get("actions") or []
    date_start = row.get("date_start")
    return MetaInsights(
        date=date.fromisoformat(date_start) if date_start else date.today(),
        impressions=int(float(row.get("impressions", 0) or 0)),
        clicks=int(float(row.get("clicks", 0) or 0)),
        spend=round(float(row.get("spend", 0) or 0), 2),
        leads=_sum_actions(actions, _LEAD_ACTIONS),
        conversions=_sum_actions(actions, _CONVERSION_ACTIONS),
        revenue=_sum_values(row.get("action_values"), _CONVERSION_ACTIONS),
    )


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
