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
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

_BASE = "https://googleads.googleapis.com/{version}"
_TIMEOUT = 30.0
_CAMPAIGN_GAQL = (
    "SELECT segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros "
    "FROM campaign "
    "WHERE campaign.advertising_channel_type = 'LOCAL_SERVICES' "
    "AND segments.date DURING LAST_90_DAYS"
)
# lead_charged=true — a lead InWork's client was actually billed for, as
# opposed to a declined/disputed one that never counted as a real lead.
_LEADS_GAQL = (
    "SELECT local_services_lead.id, local_services_lead.lead_type, "
    "local_services_lead.lead_charged, local_services_lead.lead_status, "
    "local_services_lead.creation_date_time "
    "FROM local_services_lead "
    "WHERE segments.date DURING LAST_90_DAYS"
)


class LsaClient:
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
        """Customer ids (digits) this token can access — same discovery path
        as regular Google Ads; an LSA account is just a customer id."""
        url = f"{_BASE.format(version=self._s.google_ads_api_version)}/customers:listAccessibleCustomers"
        data = await self._request("GET", url, access_token)
        return [rn.split("/")[-1] for rn in (data.get("resourceNames") or [])]

    async def fetch_daily_insights(
        self, access_token: str, customer_id: str, *, days: int = 90
    ) -> list[dict]:
        """One row per day over the last ``days`` days — the historical trend a
        dashboard chart needs, not a single rolled-up total."""
        cid = (customer_id or "").replace("-", "")
        base = f"{_BASE.format(version=self._s.google_ads_api_version)}/customers/{cid}/googleAds:searchStream"
        campaign_data = await self._request(
            "POST", base, access_token, json={"query": _CAMPAIGN_GAQL}
        )
        leads_data = await self._request("POST", base, access_token, json={"query": _LEADS_GAQL})
        return _normalize(campaign_data, leads_data)

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
                code="lsa_unreachable",
                status_code=502,
            ) from exc
        payload = _safe_json(resp)
        if resp.status_code >= 400 or (isinstance(payload, dict) and "error" in payload):
            err = payload.get("error") if isinstance(payload, dict) else None
            message = (err or {}).get("message") if isinstance(err, dict) else resp.text[:200]
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
