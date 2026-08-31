"""API tests: the Platform Insights sync (campaigns/ad sets/ads, daily metrics,
recommendations, and derived delivery issues) that piggybacks on the Meta sync.

Meta's network is faked (no real app in the hermetic suite); this exercises
the full normalize → upsert path against the new Platform Insights tables.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.google.ads import GoogleAdsClient
from app.integrations.google.lsa import LsaClient
from app.integrations.google.oauth import GoogleOAuthClient
from app.integrations.meta.client import MetaClient
from app.integrations.meta.oauth import MetaOAuthClient
from app.models.platform_insight import (
    PlatformAd,
    PlatformAdSet,
    PlatformCampaign,
    PlatformDeliveryIssue,
    PlatformMetricDaily,
    PlatformRecommendation,
)
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


@pytest.fixture
def meta_configured(monkeypatch):
    s = get_settings().integrations
    monkeypatch.setattr(s, "meta_app_id", "app123")
    monkeypatch.setattr(s, "meta_app_secret", "secret456")
    monkeypatch.setattr(s, "meta_redirect_uri", "https://app.inwork.com/oauth/meta/callback")
    return s


@pytest.fixture
def fake_meta_full(monkeypatch):
    """Connect flow + a realistic campaign/ad-set/ad/metrics/recommendation
    payload, so the auto-sync-after-connect exercises the full normalize →
    upsert path once."""

    async def exchange_code(self, code):
        return {"access_token": "short-token", "expires_in": 3600}

    async def exchange_long_lived(self, short_token):
        return {"access_token": "long-lived-token", "expires_in": 5_184_000}

    async def list_ad_accounts(self, token):
        return [{"account_id": "act_999", "name": "Acme Ad Account"}]

    async def no_insights_yet(self, token, ad_account_id, *, date_preset="last_90d"):
        return []

    async def hierarchy(self, token, ad_account_id):
        assert token == "long-lived-token" and ad_account_id == "act_999"
        return {
            "campaigns": [
                {
                    "id": "cmp_1",
                    "name": "Spring Sale",
                    "objective": "OUTCOME_SALES",
                    "status": "ACTIVE",
                    "effective_status": "ACTIVE",
                    "daily_budget": "5000",
                    "lifetime_budget": None,
                    "start_time": "2026-01-01T00:00:00+0000",
                    "stop_time": None,
                }
            ],
            "ad_sets": [
                {
                    "id": "adset_1",
                    "campaign_id": "cmp_1",
                    "name": "Warm audience",
                    "status": "ACTIVE",
                    "effective_status": "ACTIVE",
                    "daily_budget": "2500",
                    "lifetime_budget": None,
                    "targeting": {"age_min": 25, "age_max": 45},
                }
            ],
            "ads": [
                {
                    "id": "ad_1",
                    "adset_id": "adset_1",
                    "name": "Carousel v1",
                    "status": "ACTIVE",
                    "effective_status": "DISAPPROVED",
                    "issues_info": [{"error_message": "Image text too large"}],
                    "ad_review_feedback": None,
                    "creative": {"id": "creative_1"},
                }
            ],
        }

    async def campaign_metrics(self, token, ad_account_id, *, date_preset="last_90d"):
        return [
            {
                "campaign_id": "cmp_1",
                "date_start": "2026-08-01",
                "impressions": 10000,
                "clicks": 300,
                "spend": "450.50",
                "reach": 8000,
                "frequency": "1.25",
                "cpm": "45.05",
                "cpc": "1.50",
                "actions": [{"action_type": "purchase", "value": "12"}],
                "action_values": [{"action_type": "purchase", "value": "960.00"}],
                "cost_per_action_type": [{"action_type": "purchase", "value": "37.5"}],
            }
        ]

    async def recommendations(self, token, ad_account_id):
        return [
            {
                "code": "1234",
                "title": "Broaden your audience",
                "message": "This ad set's audience may be too narrow.",
                "importance": "HIGH",
            }
        ]

    monkeypatch.setattr(MetaOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(MetaOAuthClient, "exchange_long_lived", exchange_long_lived)
    monkeypatch.setattr(MetaOAuthClient, "list_ad_accounts", list_ad_accounts)
    monkeypatch.setattr(MetaClient, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(MetaClient, "fetch_campaign_hierarchy", hierarchy)
    monkeypatch.setattr(MetaClient, "fetch_campaign_metrics_daily", campaign_metrics)
    monkeypatch.setattr(MetaClient, "fetch_recommendations", recommendations)


def test_connect_syncs_platform_insights(
    client, admin_headers: dict, db_session: Session, meta_configured, fake_meta_full
):
    cid = _client_id(client, admin_headers)
    start = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers
    ).json()
    resp = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/complete",
        headers=admin_headers,
        json={"code": "auth-code-abc", "state": start["state"], "ad_account_id": "act_999"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "connected"  # platform-insights failure must not flip this

    client_uuid = uuid.UUID(cid)

    campaign = db_session.scalar(
        select(PlatformCampaign).where(PlatformCampaign.client_id == client_uuid)
    )
    assert campaign is not None
    assert campaign.external_id == "cmp_1"
    assert campaign.name == "Spring Sale"
    assert float(campaign.daily_budget) == 50.0  # cents -> currency

    ad_set = db_session.scalar(select(PlatformAdSet).where(PlatformAdSet.client_id == client_uuid))
    assert ad_set is not None
    assert ad_set.campaign_id == campaign.id
    assert ad_set.targeting_summary == {"age_min": 25, "age_max": 45}

    ad = db_session.scalar(select(PlatformAd).where(PlatformAd.client_id == client_uuid))
    assert ad is not None
    assert ad.ad_set_id == ad_set.id
    assert ad.effective_status == "DISAPPROVED"

    metric = db_session.scalar(
        select(PlatformMetricDaily).where(PlatformMetricDaily.client_id == client_uuid)
    )
    assert metric is not None
    assert metric.entity_type == "campaign" and metric.entity_id == "cmp_1"
    assert metric.impressions == 10000
    assert float(metric.spend) == 450.50
    assert metric.conversions == 12
    assert float(metric.revenue) == 960.0

    rec = db_session.scalar(
        select(PlatformRecommendation).where(PlatformRecommendation.client_id == client_uuid)
    )
    assert rec is not None
    assert rec.title == "Broaden your audience"
    assert rec.status == "open"

    issues = db_session.scalars(
        select(PlatformDeliveryIssue).where(PlatformDeliveryIssue.client_id == client_uuid)
    ).all()
    reasons = {i.reason for i in issues}
    assert "status_mismatch" in reasons  # ad: status=ACTIVE, effective_status=DISAPPROVED
    assert "disapproved_or_flagged" in reasons  # ad.issues_info was non-empty


def test_sync_upserts_in_place_not_duplicated(
    client, admin_headers: dict, db_session: Session, meta_configured, fake_meta_full
):
    cid = _client_id(client, admin_headers)
    start = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers
    ).json()
    client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/complete",
        headers=admin_headers,
        json={"code": "auth-code-abc", "state": start["state"], "ad_account_id": "act_999"},
    )
    # Connect already auto-synced once; sync again explicitly.
    resp = client.post(f"{API}/clients/{cid}/integrations/meta/sync", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    client_uuid = uuid.UUID(cid)
    campaigns = db_session.scalars(
        select(PlatformCampaign).where(PlatformCampaign.client_id == client_uuid)
    ).all()
    assert len(campaigns) == 1  # re-synced in place, not duplicated


def _connect_meta(client, admin_headers, cid):
    start = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers
    ).json()
    resp = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/complete",
        headers=admin_headers,
        json={"code": "auth-code-abc", "state": start["state"], "ad_account_id": "act_999"},
    )
    assert resp.status_code == 200, resp.text


def test_list_and_get_campaign_via_api(
    client, admin_headers: dict, meta_configured, fake_meta_full
):
    cid = _client_id(client, admin_headers)
    _connect_meta(client, admin_headers, cid)

    listed = client.get(
        f"{API}/clients/{cid}/platform-insights/meta/campaigns", headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    campaign = body["items"][0]
    assert campaign["external_id"] == "cmp_1"
    assert campaign["name"] == "Spring Sale"

    detail = client.get(
        f"{API}/clients/{cid}/platform-insights/meta/campaigns/{campaign['id']}",
        headers=admin_headers,
    )
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert len(detail_body["ad_sets"]) == 1
    assert len(detail_body["ad_sets"][0]["ads"]) == 1
    assert detail_body["ad_sets"][0]["ads"][0]["effective_status"] == "DISAPPROVED"

    metrics = client.get(
        f"{API}/clients/{cid}/platform-insights/meta/campaigns/{campaign['id']}/metrics",
        headers=admin_headers,
    )
    assert metrics.status_code == 200, metrics.text
    metrics_body = metrics.json()
    assert metrics_body["entity_id"] == "cmp_1"
    assert len(metrics_body["items"]) == 1
    assert metrics_body["items"][0]["impressions"] == 10000


def test_recommendations_and_delivery_issues_via_api(
    client, admin_headers: dict, meta_configured, fake_meta_full
):
    cid = _client_id(client, admin_headers)
    _connect_meta(client, admin_headers, cid)

    recs = client.get(
        f"{API}/clients/{cid}/platform-insights/meta/recommendations", headers=admin_headers
    )
    assert recs.status_code == 200, recs.text
    assert recs.json()["total"] == 1
    rec_id = recs.json()["items"][0]["id"]

    dismissed = client.post(
        f"{API}/clients/{cid}/platform-insights/meta/recommendations/{rec_id}/dismiss",
        headers=admin_headers,
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"

    open_recs = client.get(
        f"{API}/clients/{cid}/platform-insights/meta/recommendations?status=open",
        headers=admin_headers,
    )
    assert open_recs.json()["total"] == 0

    issues = client.get(
        f"{API}/clients/{cid}/platform-insights/meta/delivery-issues", headers=admin_headers
    )
    assert issues.status_code == 200, issues.text
    assert issues.json()["total"] >= 1
    issue_id = issues.json()["items"][0]["id"]

    resolved = client.post(
        f"{API}/clients/{cid}/platform-insights/meta/delivery-issues/{issue_id}/resolve",
        headers=admin_headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"


# ---- Google Ads (mirrors the Meta suite above) ----------------------------- #


@pytest.fixture
def google_configured(monkeypatch):
    s = get_settings().integrations
    monkeypatch.setattr(s, "google_client_id", "gid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "gsecret")
    monkeypatch.setattr(s, "google_redirect_uri", "https://app.inwork.com/oauth/google/callback")
    monkeypatch.setattr(s, "google_developer_token", "devtoken123")
    return s


@pytest.fixture
def fake_google_ads_full(monkeypatch):
    """Connect flow + a realistic campaign/ad-group/ad/metrics/recommendation
    payload (Google's REST JSON shape — camelCase, int64s as strings), so the
    auto-sync-after-connect exercises the full normalize -> upsert path once."""

    async def exchange_code(self, code):
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_accessible_customers(self, token):
        return ["9990001111"]

    async def no_linked_clients(self, token, manager_customer_id):
        return []

    async def no_insights_yet(self, access_token, customer_id, *, login_customer_id=None, days=90):
        return []

    async def hierarchy(self, access_token, customer_id, *, login_customer_id=None):
        assert access_token == "g-access" and customer_id == "9990001111"
        return {
            "campaigns": [
                {
                    "campaign": {
                        "id": "cmp_1",
                        "name": "Search — Brand",
                        "status": "ENABLED",
                        "advertisingChannelType": "SEARCH",
                        "primaryStatus": "MISCONFIGURED",
                        "primaryStatusReasons": ["CAMPAIGN_BUDGET_TOO_LOW"],
                        "startDate": "2026-01-01",
                        "endDate": "",
                    },
                    "campaignBudget": {"amountMicros": "5000000"},
                }
            ],
            "ad_groups": [
                {
                    "adGroup": {
                        "id": "adg_1",
                        "name": "Core keywords",
                        "campaign": "customers/9990001111/campaigns/cmp_1",
                        "status": "ENABLED",
                        "type": "SEARCH_STANDARD",
                    }
                }
            ],
            "ads": [
                {
                    "adGroupAd": {
                        "ad": {"id": "ad_1", "name": "RSA v1", "type": "RESPONSIVE_SEARCH_AD"},
                        "adGroup": "customers/9990001111/adGroups/adg_1",
                        "status": "ENABLED",
                        "policySummary": {
                            "approvalStatus": "DISAPPROVED",
                            "reviewStatus": "REVIEWED",
                            "policyTopicEntries": [{"topic": "MISLEADING_CONTENT"}],
                        },
                    }
                }
            ],
        }

    async def campaign_metrics(
        self, access_token, customer_id, *, login_customer_id=None, days=90
    ):
        return [
            {
                "campaign": {"id": "cmp_1"},
                "segments": {"date": "2026-08-01"},
                "metrics": {
                    "impressions": "10000",
                    "clicks": "300",
                    "costMicros": "450500000",
                    "conversions": 12.0,
                    "conversionsValue": 960.0,
                },
            }
        ]

    async def recommendations(self, access_token, customer_id, *, login_customer_id=None):
        return [
            {
                "recommendation": {
                    "resourceName": "customers/9990001111/recommendations/rec1",
                    "type": "CAMPAIGN_BUDGET",
                    "campaign": "customers/9990001111/campaigns/cmp_1",
                    "dismissed": False,
                }
            }
        ]

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(GoogleAdsClient, "list_accessible_customers", list_accessible_customers)
    monkeypatch.setattr(GoogleAdsClient, "list_customer_clients", no_linked_clients)
    monkeypatch.setattr(GoogleAdsClient, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_hierarchy", hierarchy)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_metrics_daily", campaign_metrics)
    monkeypatch.setattr(GoogleAdsClient, "fetch_recommendations", recommendations)


def _connect_google_ads(client, admin_headers, cid):
    start = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/start", headers=admin_headers
    ).json()
    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/complete",
        headers=admin_headers,
        json={"code": "g-auth-code", "state": start["state"], "ad_account_id": "9990001111"},
    )
    assert resp.status_code == 200, resp.text
    return resp


def test_connect_syncs_google_ads_platform_insights(
    client, admin_headers: dict, db_session: Session, google_configured, fake_google_ads_full
):
    cid = _client_id(client, admin_headers)
    resp = _connect_google_ads(client, admin_headers, cid)
    assert resp.json()["status"] == "connected"  # platform-insights failure must not flip this

    client_uuid = uuid.UUID(cid)

    campaign = db_session.scalar(
        select(PlatformCampaign).where(
            PlatformCampaign.client_id == client_uuid,
            PlatformCampaign.integration_key == "google_ads",
        )
    )
    assert campaign is not None
    assert campaign.external_id == "cmp_1"
    assert campaign.name == "Search — Brand"
    assert campaign.objective == "SEARCH"
    assert campaign.effective_status == "MISCONFIGURED"
    assert float(campaign.daily_budget) == 5.0  # micros -> currency

    ad_set = db_session.scalar(
        select(PlatformAdSet).where(PlatformAdSet.client_id == client_uuid)
    )
    assert ad_set is not None
    assert ad_set.campaign_id == campaign.id
    assert ad_set.external_id == "adg_1"

    ad = db_session.scalar(select(PlatformAd).where(PlatformAd.client_id == client_uuid))
    assert ad is not None
    assert ad.ad_set_id == ad_set.id
    assert ad.effective_status == "DISAPPROVED"

    metric = db_session.scalar(
        select(PlatformMetricDaily).where(
            PlatformMetricDaily.client_id == client_uuid,
            PlatformMetricDaily.integration_key == "google_ads",
        )
    )
    assert metric is not None
    assert metric.entity_type == "campaign" and metric.entity_id == "cmp_1"
    assert metric.impressions == 10000
    assert float(metric.spend) == 450.50
    assert metric.conversions == 12
    assert float(metric.revenue) == 960.0

    rec = db_session.scalar(
        select(PlatformRecommendation).where(
            PlatformRecommendation.client_id == client_uuid,
            PlatformRecommendation.integration_key == "google_ads",
        )
    )
    assert rec is not None
    assert rec.code == "CAMPAIGN_BUDGET"
    assert rec.entity_id == "cmp_1"
    assert rec.status == "open"

    issues = db_session.scalars(
        select(PlatformDeliveryIssue).where(
            PlatformDeliveryIssue.client_id == client_uuid,
            PlatformDeliveryIssue.integration_key == "google_ads",
        )
    ).all()
    reasons = {i.reason for i in issues}
    assert "primary_status_misconfigured" in reasons  # campaign.primaryStatus
    assert "policy_disapproved" in reasons  # ad policy_summary.approval_status


def test_list_google_ads_campaign_via_api(
    client, admin_headers: dict, google_configured, fake_google_ads_full
):
    cid = _client_id(client, admin_headers)
    _connect_google_ads(client, admin_headers, cid)

    listed = client.get(
        f"{API}/clients/{cid}/platform-insights/google_ads/campaigns", headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "cmp_1"


# ---- Google LSA (campaigns only — no ad-group/ad tier) --------------------- #


@pytest.fixture
def fake_google_lsa_full(monkeypatch):
    """Connect flow + a realistic LSA campaign/metrics/recommendation payload.
    LSA has no ad-group/ad hierarchy (no keywords or creatives), so the
    hierarchy fixture only returns campaigns."""

    async def exchange_code(self, code):
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_accessible_customers(self, token):
        return ["2336702039"]

    async def no_linked_clients(self, token, manager_customer_id):
        return []

    async def no_insights_yet(self, access_token, customer_id, *, days=90):
        return []

    async def hierarchy(self, access_token, customer_id):
        assert access_token == "g-access" and customer_id == "2336702039"
        return {
            "campaigns": [
                {
                    "campaign": {
                        "id": "lsa_cmp_1",
                        "name": "Tampa Bay LSA",
                        "status": "ENABLED",
                        "advertisingChannelType": "LOCAL_SERVICES",
                        "primaryStatus": "ELIGIBLE",
                        "primaryStatusReasons": [],
                        "startDate": "2026-02-01",
                        "endDate": "",
                    },
                    "campaignBudget": {"amountMicros": "8000000"},
                }
            ],
            "ad_groups": [],
            "ads": [],
        }

    async def campaign_metrics(self, access_token, customer_id, *, days=90):
        return [
            {
                "campaign": {"id": "lsa_cmp_1"},
                "segments": {"date": "2026-08-01"},
                "metrics": {
                    "impressions": "500",
                    "clicks": "20",
                    "costMicros": "80000000",
                    "conversions": 4.0,
                    "conversionsValue": 0.0,
                },
            }
        ]

    async def recommendations(self, access_token, customer_id):
        return []

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(LsaClient, "list_accessible_customers", list_accessible_customers)
    monkeypatch.setattr(LsaClient, "list_customer_clients", no_linked_clients)
    monkeypatch.setattr(LsaClient, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(LsaClient, "fetch_campaign_hierarchy", hierarchy)
    monkeypatch.setattr(LsaClient, "fetch_campaign_metrics_daily", campaign_metrics)
    monkeypatch.setattr(LsaClient, "fetch_recommendations", recommendations)


def test_connect_syncs_google_lsa_platform_insights(
    client, admin_headers: dict, db_session: Session, google_configured, fake_google_lsa_full
):
    cid = _client_id(client, admin_headers)
    start = client.post(
        f"{API}/clients/{cid}/integrations/google_lsa/oauth/start", headers=admin_headers
    ).json()
    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_lsa/oauth/complete",
        headers=admin_headers,
        json={"code": "g-auth-code", "state": start["state"], "ad_account_id": "2336702039"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "connected"

    client_uuid = uuid.UUID(cid)

    campaign = db_session.scalar(
        select(PlatformCampaign).where(
            PlatformCampaign.client_id == client_uuid,
            PlatformCampaign.integration_key == "google_lsa",
        )
    )
    assert campaign is not None
    assert campaign.external_id == "lsa_cmp_1"
    assert campaign.name == "Tampa Bay LSA"
    assert float(campaign.daily_budget) == 8.0

    # No ad-group/ad rows for LSA.
    assert (
        db_session.scalar(
            select(PlatformAdSet).where(PlatformAdSet.client_id == client_uuid)
        )
        is None
    )

    metric = db_session.scalar(
        select(PlatformMetricDaily).where(
            PlatformMetricDaily.client_id == client_uuid,
            PlatformMetricDaily.integration_key == "google_lsa",
        )
    )
    assert metric is not None
    assert metric.entity_id == "lsa_cmp_1"
    assert metric.impressions == 500
    assert float(metric.spend) == 80.0
    assert metric.conversions == 4
