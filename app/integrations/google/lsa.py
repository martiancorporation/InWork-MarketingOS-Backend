"""Google Local Services Ads — read LSA performance via the standard Ads API.

**Not** the dedicated Local Services API (``localservices.googleapis.com``) —
that one only returns data for LSA accounts linked under an Ads manager (MCC)
account we control, and returns empty (not an error) otherwise. Our clients
typically self-manage their own LSA account rather than sit under our MCC, so
that path is a dead end here.

Instead: LSA campaigns show up as a normal ``LOCAL_SERVICES`` campaign type in
the regular Google Ads API, queryable directly against the client's own
customer id (no manager link needed) — same ``searchStream`` + GAQL mechanism
as ``GoogleAdsClient``. Two queries, both broken down **by day**: campaign-level
spend/impressions (via ``segments.date``), and the ``local_services_lead``
resource for lead type/charged status/dispute state (grouped by each lead's own
``creation_date_time`` — ``local_services_lead`` has no ``metrics``, so it isn't
assumed to support ``segments.date`` as a group-by the way metric resources do).

Trade-off: this path does not expose star rating, review count, phone
responsiveness rate, or ZIP-level breakdown — those live only in the Local
Services API (the dead end above) or the separate Business Profile API (needs
its own Google-side manual access approval).

Never sends ``login-customer-id``: confirmed with the client that none of
their LSA accounts are queried through an MCC (client GM Debanjan Dey,
2026-08-25 email) — unlike ``GoogleAdsClient``, which does need it for a
couple of specific real sub-accounts (operator-entered per connection, see
``Integration.login_customer_id``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger("app.integrations.google.lsa")

_BASE = "https://googleads.googleapis.com/{version}"
_TIMEOUT = 30.0
_CAMPAIGN_GAQL = (
    "SELECT segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros "
    "FROM campaign "
    "WHERE campaign.advertising_channel_type = 'LOCAL_SERVICES' "
    "AND segments.date BETWEEN '{start}' AND '{end}'"
)
# lead_charged=true — a lead InWork's client was actually billed for, as
# opposed to a declined/disputed one that never counted as a real lead.
_LEADS_GAQL = (
    "SELECT local_services_lead.id, local_services_lead.lead_type, "
    "local_services_lead.lead_charged, local_services_lead.lead_status, "
    "local_services_lead.creation_date_time "
    "FROM local_services_lead "
    "WHERE segments.date BETWEEN '{start}' AND '{end}'"
)


def _date_range(days: int) -> tuple[str, str]:
    """GAQL has no ``DURING LAST_N_DAYS`` for arbitrary N — only a fixed set of
    named ranges. An explicit BETWEEN range is the only way to honor a
    caller-supplied day count."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


_CUSTOMER_CLIENT_GAQL = (
    "SELECT customer_client.client_customer, customer_client.level, "
    "customer_client.manager, customer_client.descriptive_name "
    "FROM customer_client WHERE customer_client.level > 0"
)

# Platform Insights — LSA campaigns are a normal ``campaign`` resource (channel
# type LOCAL_SERVICES), so this row shape deliberately matches
# ``app.integrations.google.ads._CAMPAIGN_GAQL`` / ``_CAMPAIGN_METRIC_GAQL``
# field-for-field: platform_insight_service.py reuses the same Google Ads
# normalizers rather than duplicating them. LSA has no ad-group/ad hierarchy
# (no keywords or creatives to manage), so there's no equivalent of
# ``_AD_GROUP_GAQL``/``_AD_GROUP_AD_GAQL`` here.
_CAMPAIGN_DETAIL_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
    "campaign.primary_status, campaign.primary_status_reasons, "
    "campaign.start_date, campaign.end_date, campaign_budget.amount_micros "
    "FROM campaign WHERE campaign.advertising_channel_type = 'LOCAL_SERVICES'"
)
_CAMPAIGN_DETAIL_METRIC_GAQL = (
    "SELECT campaign.id, segments.date, metrics.impressions, metrics.clicks, "
    "metrics.cost_micros, metrics.conversions, metrics.conversions_value "
    "FROM campaign "
    "WHERE campaign.advertising_channel_type = 'LOCAL_SERVICES' "
    "AND segments.date BETWEEN '{start}' AND '{end}'"
)
_RECOMMENDATION_GAQL = (
    "SELECT recommendation.resource_name, recommendation.type, recommendation.campaign, "
    "recommendation.impact, recommendation.dismissed "
    "FROM recommendation WHERE recommendation.dismissed = FALSE"
)


class LsaClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    def _headers(self, access_token: str, login_customer_id: str | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": self._s.google_developer_token or "",
            "Content-Type": "application/json",
        }
        if login_customer_id:
            headers["login-customer-id"] = login_customer_id.replace("-", "")
        return headers

    async def list_accessible_customers(self, access_token: str) -> list[str]:
        """Customer ids (digits) this token can access — same discovery path
        as regular Google Ads; an LSA account is just a customer id. Only
        accounts the OAuth user has *direct* access to — a manager account's
        linked clients are NOT included here (see ``list_customer_clients``)."""
        url = f"{_BASE.format(version=self._s.google_ads_api_version)}/customers:listAccessibleCustomers"
        data = await self._request("GET", url, access_token)
        return [rn.split("/")[-1] for rn in (data.get("resourceNames") or [])]

    async def list_customer_clients(
        self, access_token: str, manager_customer_id: str
    ) -> list[dict]:
        """Client accounts linked under a manager (MCC) account — not returned
        by ``list_accessible_customers``. Queried *through* the manager
        (``login-customer-id`` set to its own id, for this discovery call
        only — ``fetch_daily_insights`` still never sends it, per Debanjan's
        explicit instruction that none of the client LSA accounts are queried
        *through* a manager). Mirrors ``GoogleAdsClient.list_customer_clients``."""
        mgr = manager_customer_id.replace("-", "")
        url = (
            f"{_BASE.format(version=self._s.google_ads_api_version)}"
            f"/customers/{mgr}/googleAds:searchStream"
        )
        data = await self._request(
            "POST",
            url,
            access_token,
            json={"query": _CUSTOMER_CLIENT_GAQL},
            login_customer_id=mgr,
        )
        batches = data.get("data") if isinstance(data.get("data"), list) else [data]
        out = []
        for batch in batches or []:
            for row in (batch or {}).get("results") or []:
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
        self, access_token: str, customer_id: str, *, days: int = 90
    ) -> list[dict]:
        """One row per day over the last ``days`` days — the historical trend a
        dashboard chart needs, not a single rolled-up total."""
        cid = (customer_id or "").replace("-", "")
        base = f"{_BASE.format(version=self._s.google_ads_api_version)}/customers/{cid}/googleAds:searchStream"
        start, end = _date_range(days)
        campaign_data = await self._request(
            "POST", base, access_token, json={"query": _CAMPAIGN_GAQL.format(start=start, end=end)}
        )
        leads_data = await self._request(
            "POST", base, access_token, json={"query": _LEADS_GAQL.format(start=start, end=end)}
        )
        return _normalize(campaign_data, leads_data)

    async def fetch_campaign_hierarchy(self, access_token: str, customer_id: str) -> dict:
        """LSA campaigns only — no ad-group/ad tier (see module docstring)."""
        campaigns = await self._search(access_token, customer_id, _CAMPAIGN_DETAIL_GAQL)
        return {"campaigns": campaigns, "ad_groups": [], "ads": []}

    async def fetch_campaign_metrics_daily(
        self, access_token: str, customer_id: str, *, days: int = 90
    ) -> list[dict]:
        """One row per (campaign, day) — mirrors
        ``GoogleAdsClient.fetch_campaign_metrics_daily``."""
        start, end = _date_range(days)
        query = _CAMPAIGN_DETAIL_METRIC_GAQL.format(start=start, end=end)
        return await self._search(access_token, customer_id, query)

    async def fetch_recommendations(self, access_token: str, customer_id: str) -> list[dict]:
        """Google Ads' own account-level recommendations for this LSA customer."""
        return await self._search(access_token, customer_id, _RECOMMENDATION_GAQL)

    async def _search(self, access_token: str, customer_id: str, query: str) -> list[dict]:
        """Run a GAQL query via ``searchStream`` and return the flattened
        ``results[]`` rows, verbatim. Never sends ``login-customer-id`` — same
        rule as ``fetch_daily_insights`` (see module docstring)."""
        cid = (customer_id or "").replace("-", "")
        url = (
            f"{_BASE.format(version=self._s.google_ads_api_version)}"
            f"/customers/{cid}/googleAds:searchStream"
        )
        data = await self._request("POST", url, access_token, json={"query": query})
        batches = data.get("data") if isinstance(data.get("data"), list) else [data]
        return [row for batch in (batches or []) for row in (batch or {}).get("results") or []]

    async def _request(
        self,
        method: str,
        url: str,
        access_token: str,
        json: dict | None = None,
        *,
        login_customer_id: str | None = None,
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                resp = await http.request(
                    method, url, headers=self._headers(access_token, login_customer_id), json=json
                )
        except httpx.HTTPError as exc:
            raise AppError(
                f"Could not reach Google Ads: {exc}",
                code="lsa_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or (isinstance(payload, dict) and "error" in payload):
            err = payload.get("error") if isinstance(payload, dict) else None
            message = (err or {}).get("message") if isinstance(err, dict) else resp.text[:200]
            logger.warning("Google Ads API (LSA) rejected %s %s: %s", method, url, payload)
            raise AppError(
                f"Google Ads rejected the request: {message}",
                code="lsa_error",
                status_code=400,
            )
        return payload if isinstance(payload, dict) else {"data": payload}


def _normalize(campaign_payload: dict, leads_payload: dict) -> list[dict]:
    """Merge the two searchStream results into one row per day — campaign
    metrics keyed by ``segments.date``, charged-lead counts keyed by each
    lead's own ``creation_date_time`` (its date component)."""
    by_date: dict[date, dict] = defaultdict(
        lambda: {"impressions": 0, "clicks": 0, "cost_micros": 0, "leads": 0}
    )

    for batch in _batches(campaign_payload):
        for row in (batch or {}).get("results") or []:
            m = row.get("metrics") or {}
            segments = row.get("segments") or {}
            date_str = segments.get("date")
            d = date.fromisoformat(date_str) if date_str else date.today()
            bucket = by_date[d]
            bucket["impressions"] += int(m.get("impressions", 0) or 0)
            bucket["clicks"] += int(m.get("clicks", 0) or 0)
            bucket["cost_micros"] += int(m.get("costMicros", m.get("cost_micros", 0)) or 0)

    for batch in _batches(leads_payload):
        for row in (batch or {}).get("results") or []:
            lead = row.get("localServicesLead") or row.get("local_services_lead") or {}
            if not (lead.get("leadCharged") or lead.get("lead_charged")):
                continue
            created = lead.get("creationDateTime") or lead.get("creation_date_time")
            d = _parse_lead_date(created)
            by_date[d]["leads"] += 1

    return [
        {
            "date": d,
            "impressions": bucket["impressions"],
            "clicks": bucket["clicks"],
            "spend": round(bucket["cost_micros"] / 1_000_000, 2),
            "conversions": bucket["leads"],
            "leads": bucket["leads"],
            "revenue": 0.0,  # LSA has no advertiser-tracked revenue/attribution concept
        }
        for d, bucket in sorted(by_date.items())
    ]


def _parse_lead_date(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.today()


def _batches(payload: dict) -> list[dict]:
    data = payload.get("data")
    return data if isinstance(data, list) else [payload]


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {}
