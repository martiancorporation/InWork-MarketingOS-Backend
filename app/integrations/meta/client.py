"""Meta Marketing API (Graph) — read ad-account insights, campaign hierarchy,
and native recommendations.

Given a client's stored access token + ad-account id:
- ``fetch_daily_insights`` pulls a **day-by-day** performance series (not one
  aggregated blob) and normalizes each day into the flat shape our
  analytics/campaign layer expects. Lead and conversion counts come out of the
  Graph ``actions`` breakdown.
- ``fetch_campaign_hierarchy`` pulls every campaign → ad set → ad currently on
  the account, verbatim (raw Graph dicts) — the normalizer layer
  (``app/services/platform_insight_service.py``) turns these into rows.
- ``fetch_recommendations`` pulls Meta's own account-level
  ``/recommendations`` (delivery/optimization suggestions Meta computes
  itself — distinct from anything AI-generated in this product).
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

_INSIGHTS_FIELDS = "impressions,clicks,spend,actions,action_values,date_start"
_CAMPAIGN_INSIGHTS_FIELDS = (
    "campaign_id,impressions,clicks,spend,reach,frequency,cpm,cpc,"
    "actions,action_values,cost_per_action_type,date_start"
)
_CAMPAIGN_FIELDS = (
    "id,name,objective,status,effective_status,daily_budget,lifetime_budget,start_time,stop_time"
)
_ADSET_FIELDS = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,targeting"
_AD_FIELDS = "id,name,adset_id,status,effective_status,issues_info,ad_review_feedback,creative"


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
        account = _act(ad_account_id)
        url = f"{_GRAPH.format(version=self._s.meta_api_version)}/{account}/insights"
        params = {
            "fields": _INSIGHTS_FIELDS,
            "time_increment": 1,
            "date_preset": date_preset,
            "limit": 100,
            "access_token": access_token,
        }
        raw_rows = await self._paginated(url, params)
        return [_normalize(row) for row in raw_rows]

    async def fetch_campaign_metrics_daily(
        self, access_token: str, ad_account_id: str, *, date_preset: str = "last_90d"
    ) -> list[dict]:
        """One row per (campaign, day) — the richer field set (reach, frequency,
        cpm, cpc, per-action-type cost) that ``PlatformMetricDaily`` stores,
        raw (not squeezed into the flat ``AnalyticsDailyIn`` shape)."""
        account = _act(ad_account_id)
        url = f"{_GRAPH.format(version=self._s.meta_api_version)}/{account}/insights"
        params = {
            "level": "campaign",
            "fields": _CAMPAIGN_INSIGHTS_FIELDS,
            "time_increment": 1,
            "date_preset": date_preset,
            "limit": 100,
            "access_token": access_token,
        }
        return await self._paginated(url, params)

    async def fetch_campaign_hierarchy(self, access_token: str, ad_account_id: str) -> dict:
        """Every campaign, ad set, and ad currently on the account (raw Graph
        dicts, verbatim — normalization happens one layer up)."""
        account = _act(ad_account_id)
        base = f"{_GRAPH.format(version=self._s.meta_api_version)}/{account}"
        campaigns = await self._paginated(
            f"{base}/campaigns",
            {"fields": _CAMPAIGN_FIELDS, "limit": 100, "access_token": access_token},
        )
        ad_sets = await self._paginated(
            f"{base}/adsets",
            {"fields": _ADSET_FIELDS, "limit": 100, "access_token": access_token},
        )
        ads = await self._paginated(
            f"{base}/ads",
            {"fields": _AD_FIELDS, "limit": 100, "access_token": access_token},
        )
        return {"campaigns": campaigns, "ad_sets": ad_sets, "ads": ads}

    async def fetch_recommendations(self, access_token: str, ad_account_id: str) -> list[dict]:
        """Meta's own account-level delivery/optimization recommendations
        (``/recommendations``) — a Meta computed signal, not AI-generated."""
        account = _act(ad_account_id)
        url = f"{_GRAPH.format(version=self._s.meta_api_version)}/{account}/recommendations"
        return await self._paginated(url, {"limit": 100, "access_token": access_token})

    async def _paginated(self, url: str, params: dict) -> list[dict]:
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
                        f"Meta request failed: {message}",
                        code="meta_api_error",
                        status_code=400,
                    )
                raw_rows.extend(payload.get("data") or [])
                next_url = (payload.get("paging") or {}).get("next")
                if not next_url:
                    break
        return raw_rows


def _act(ad_account_id: str) -> str:
    return ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"


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
