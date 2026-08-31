"""Google Ads API (read-only) — pull account performance metrics.

Uses the REST ``searchStream`` endpoint with a GAQL query, authenticated by the
client's OAuth access token plus our Google-approved ``developer-token``. The
manager ``login-customer-id`` header is required only for accounts that sit
under an MCC — which ones do is operator-entered per integration
(``Integration.login_customer_id``, set at connect time), not hardcoded here,
since it varies per real client account and isn't derivable from the API.
``fetch_daily_insights`` breaks the report down **by day** (not one
aggregated blob), normalized into the same flat shape as the Meta client so
both ad platforms feed ``analytics_daily`` identically (cost is reported in
micros → dollars).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger("app.integrations.google.ads")

_BASE = "https://googleads.googleapis.com/{version}"
_TIMEOUT = 30.0
_DAILY_GAQL = (
    "SELECT segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros, "
    "metrics.conversions, metrics.conversions_value "
    "FROM customer WHERE segments.date BETWEEN '{start}' AND '{end}'"
)


_CUSTOMER_CLIENT_GAQL = (
    "SELECT customer_client.client_customer, customer_client.level, "
    "customer_client.manager, customer_client.descriptive_name "
    "FROM customer_client WHERE customer_client.level > 0"
)

# Platform Insights hierarchy — mirrors what MetaClient.fetch_campaign_hierarchy
# pulls (campaign -> ad group -> ad), raw GAQL rows returned verbatim;
# normalization happens one layer up in platform_insight_service.py.
_CAMPAIGN_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
    "campaign.primary_status, campaign.primary_status_reasons, "
    "campaign.start_date, campaign.end_date, campaign_budget.amount_micros "
    "FROM campaign"
)
_AD_GROUP_GAQL = (
    "SELECT ad_group.id, ad_group.name, ad_group.campaign, ad_group.status, ad_group.type "
    "FROM ad_group"
)
_AD_GROUP_AD_GAQL = (
    "SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.ad.type, "
    "ad_group_ad.ad_group, ad_group_ad.status, "
    "ad_group_ad.policy_summary.approval_status, ad_group_ad.policy_summary.review_status, "
    "ad_group_ad.policy_summary.policy_topic_entries "
    "FROM ad_group_ad"
)
_CAMPAIGN_METRIC_GAQL = (
    "SELECT campaign.id, segments.date, metrics.impressions, metrics.clicks, "
    "metrics.cost_micros, metrics.conversions, metrics.conversions_value "
    "FROM campaign WHERE segments.date BETWEEN '{start}' AND '{end}'"
)
# Only a fraction of recommendation types populate `impact` — selected best
# effort; `dismissed` recommendations are excluded (nothing actionable there).
_RECOMMENDATION_GAQL = (
    "SELECT recommendation.resource_name, recommendation.type, recommendation.campaign, "
    "recommendation.impact, recommendation.dismissed "
    "FROM recommendation WHERE recommendation.dismissed = FALSE"
)


def _daily_gaql(days: int) -> str:
    """GAQL has no ``DURING LAST_N_DAYS`` for arbitrary N — only a fixed set of
    named ranges (LAST_7_DAYS, LAST_30_DAYS, ...). An explicit BETWEEN range is
    the only way to honor a caller-supplied day count."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    return _DAILY_GAQL.format(start=start.isoformat(), end=end.isoformat())


class GoogleAdsClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    def _headers(self, access_token: str, login_customer_id: str | None) -> dict:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": self._s.google_developer_token or "",
            "Content-Type": "application/json",
        }
        if login_customer_id:
            headers["login-customer-id"] = login_customer_id.replace("-", "")
        return headers

    async def list_accessible_customers(self, access_token: str) -> list[str]:
        """Customer ids (digits) this token can access, e.g. ['1234567890'].
        Only accounts the OAuth user has *direct* access to — a manager
        account's linked clients are NOT included here even though the token
        can query them (see ``list_customer_clients``)."""
        url = f"{_BASE.format(version=self._s.google_ads_api_version)}/customers:listAccessibleCustomers"
        data = await self._request("GET", url, access_token, login_customer_id=None)
        # resourceNames look like "customers/1234567890".
        return [rn.split("/")[-1] for rn in (data.get("resourceNames") or [])]

    async def list_customer_clients(
        self, access_token: str, manager_customer_id: str
    ) -> list[dict]:
        """Client accounts linked under a manager (MCC) account — not returned
        by ``list_accessible_customers``. Queried *through* the manager
        (``login-customer-id`` set to its own id), per Google's documented
        ``customer_client`` account-hierarchy pattern. Returns one dict per
        descendant: ``{"id", "name", "manager"}``."""
        mgr = manager_customer_id.replace("-", "")
        rows = await self._search(access_token, mgr, _CUSTOMER_CLIENT_GAQL, login_customer_id=mgr)
        out = []
        for row in rows:
            cc = row.get("customerClient") or row.get("customer_client") or {}
            resource = cc.get("clientCustomer") or cc.get("client_customer") or ""
            cid = resource.split("/")[-1] if resource else None
            if not cid:
                continue
            out.append(
                {
                    "id": cid,
                    "name": cc.get("descriptiveName") or cc.get("descriptive_name"),
                    "manager": bool(cc.get("manager")),
                }
            )
        return out

    async def fetch_daily_insights(
        self,
        access_token: str,
        customer_id: str,
        *,
        login_customer_id: str | None = None,
        days: int = 90,
    ) -> list[dict]:
        """One row per day over the last ``days`` days — the historical trend a
        dashboard chart needs, not a single rolled-up total. ``login_customer_id``
        is the MCC id to query *through*, when this customer is a manager-linked
        sub-account (omit for a standalone account)."""
        cid = (customer_id or "").replace("-", "")
        url = (
            f"{_BASE.format(version=self._s.google_ads_api_version)}"
            f"/customers/{cid}/googleAds:searchStream"
        )
        data = await self._request(
            "POST",
            url,
            access_token,
            login_customer_id=login_customer_id,
            json={"query": _daily_gaql(days)},
        )
        return _normalize(data)

    async def fetch_campaign_hierarchy(
        self, access_token: str, customer_id: str, *, login_customer_id: str | None = None
    ) -> dict:
        """Every campaign, ad group, and ad currently on the account (raw GAQL
        rows, verbatim — normalization happens one layer up), mirroring
        ``MetaClient.fetch_campaign_hierarchy``."""
        campaigns = await self._search(
            access_token, customer_id, _CAMPAIGN_GAQL, login_customer_id=login_customer_id
        )
        ad_groups = await self._search(
            access_token, customer_id, _AD_GROUP_GAQL, login_customer_id=login_customer_id
        )
        ads = await self._search(
            access_token, customer_id, _AD_GROUP_AD_GAQL, login_customer_id=login_customer_id
        )
        return {"campaigns": campaigns, "ad_groups": ad_groups, "ads": ads}

    async def fetch_campaign_metrics_daily(
        self,
        access_token: str,
        customer_id: str,
        *,
        login_customer_id: str | None = None,
        days: int = 90,
    ) -> list[dict]:
        """One row per (campaign, day) — mirrors
        ``MetaClient.fetch_campaign_metrics_daily``."""
        end = date.today()
        start = end - timedelta(days=days - 1)
        query = _CAMPAIGN_METRIC_GAQL.format(start=start.isoformat(), end=end.isoformat())
        return await self._search(
            access_token, customer_id, query, login_customer_id=login_customer_id
        )

    async def fetch_recommendations(
        self, access_token: str, customer_id: str, *, login_customer_id: str | None = None
    ) -> list[dict]:
        """Google Ads' own account-level recommendations — a Google-computed
        signal, not AI-generated, mirroring
        ``MetaClient.fetch_recommendations``."""
        return await self._search(
            access_token,
            customer_id,
            _RECOMMENDATION_GAQL,
            login_customer_id=login_customer_id,
        )

    async def _search(
        self,
        access_token: str,
        customer_id: str,
        query: str,
        *,
        login_customer_id: str | None,
    ) -> list[dict]:
        """Run a GAQL query via ``searchStream`` and return the flattened
        ``results[]`` rows, verbatim (camelCase JSON keys, as Google returns
        them)."""
        cid = (customer_id or "").replace("-", "")
        url = (
            f"{_BASE.format(version=self._s.google_ads_api_version)}"
            f"/customers/{cid}/googleAds:searchStream"
        )
        data = await self._request(
            "POST", url, access_token, login_customer_id=login_customer_id, json={"query": query}
        )
        batches = data.get("data") if isinstance(data.get("data"), list) else [data]
        return [row for batch in (batches or []) for row in (batch or {}).get("results") or []]

    async def _request(
        self,
        method: str,
        url: str,
        access_token: str,
        *,
        login_customer_id: str | None,
        json: dict | None = None,
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.request(
                    method,
                    url,
                    headers=self._headers(access_token, login_customer_id),
                    json=json,
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
            # The trimmed `message` sent to the frontend often just says
            # "The caller does not have permission" — the real diagnosis lives
            # in `error.details[].errors[].errorCode` (e.g.
            # USER_PERMISSION_DENIED vs DEVELOPER_TOKEN_NOT_APPROVED vs
            # CUSTOMER_NOT_ENABLED). Log the full body so a real failure is
            # diagnosable from the server logs, not by guessing.
            logger.warning("Google Ads API rejected %s %s: %s", method, url, payload)
            raise AppError(
                f"Google Ads rejected the request: {message}",
                code="google_ads_error",
                status_code=400,
            )
        return payload if isinstance(payload, dict) else {"data": payload}


def _normalize(payload: dict) -> list[dict]:
    """``searchStream`` returns a list of batches, each with ``results[]`` rows
    carrying a ``segments.date`` + ``metrics`` object — one row per day."""
    batches = payload.get("data") if isinstance(payload.get("data"), list) else [payload]
    rows = []
    for batch in batches or []:
        for row in (batch or {}).get("results") or []:
            m = row.get("metrics") or {}
            segments = row.get("segments") or {}
            date_str = segments.get("date")
            rows.append(
                {
                    "date": date.fromisoformat(date_str) if date_str else date.today(),
                    "impressions": int(m.get("impressions", 0) or 0),
                    "clicks": int(m.get("clicks", 0) or 0),
                    "spend": round(
                        int(m.get("costMicros", m.get("cost_micros", 0)) or 0) / 1_000_000, 2
                    ),
                    "conversions": int(float(m.get("conversions", 0) or 0)),
                    # Google Ads "conversions" are the lead/action count.
                    "leads": int(float(m.get("conversions", 0) or 0)),
                    "revenue": round(
                        float(m.get("conversionsValue", m.get("conversions_value", 0)) or 0), 2
                    ),
                }
            )
    return rows


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {}
