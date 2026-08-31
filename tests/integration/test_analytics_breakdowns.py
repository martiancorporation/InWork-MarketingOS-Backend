"""API tests: GA4/Search Console Analytics Breakdowns (top pages, channels,
devices, search queries) — the OAuth connect flow triggers an auto-sync,
same non-fatal-failure contract as Platform Insights.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.google.ga4 import Ga4Client
from app.integrations.google.oauth import GoogleOAuthClient
from app.integrations.google.search_console import SearchConsoleClient
from app.models.analytics_breakdown import AnalyticsBreakdown
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


@pytest.fixture
def google_configured(monkeypatch):
    s = get_settings().integrations
    monkeypatch.setattr(s, "google_client_id", "gid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "gsecret")
    monkeypatch.setattr(s, "google_redirect_uri", "https://app.inwork.com/oauth/google/callback")
    return s


@pytest.fixture
def fake_ga4_full(monkeypatch):
    async def exchange_code(self, code):
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_properties(self, token):
        return ["531798814"]

    async def no_insights_yet(self, access_token, property_id, *, days=90):
        return []

    async def breakdowns(self, access_token, property_id, *, days=90):
        assert access_token == "g-access" and property_id == "531798814"
        return {
            "top_page": [
                {"dimension": "/", "metrics": {"sessions": 120, "page_views": 200}},
                {"dimension": "/pricing", "metrics": {"sessions": 40, "page_views": 55}},
            ],
            "channel": [{"dimension": "Organic Search", "metrics": {"sessions": 90}}],
            "device": [{"dimension": "mobile", "metrics": {"sessions": 100}}],
        }

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(Ga4Client, "list_properties", list_properties)
    monkeypatch.setattr(Ga4Client, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(Ga4Client, "fetch_breakdowns", breakdowns)


@pytest.fixture
def fake_search_console_full(monkeypatch):
    async def exchange_code(self, code):
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_sites(self, token):
        return ["https://tonysgarageinc.com/"]

    async def no_insights_yet(self, access_token, site_url, *, days=90):
        return []

    async def breakdowns(self, access_token, site_url, *, days=90):
        assert access_token == "g-access" and site_url == "https://tonysgarageinc.com/"
        return {
            "top_query": [
                {
                    "dimension": "roofing near me",
                    "metrics": {"clicks": 12, "impressions": 300, "ctr": 0.04, "position": 3.2},
                }
            ],
            "top_page": [
                {
                    "dimension": "https://tonysgarageinc.com/",
                    "metrics": {"clicks": 20, "impressions": 500, "ctr": 0.04, "position": 5.1},
                }
            ],
            "device": [
                {
                    "dimension": "MOBILE",
                    "metrics": {"clicks": 18, "impressions": 400, "ctr": 0.045, "position": 4.0},
                }
            ],
        }

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(SearchConsoleClient, "list_sites", list_sites)
    monkeypatch.setattr(SearchConsoleClient, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(SearchConsoleClient, "fetch_breakdowns", breakdowns)


def _connect(client, admin_headers, cid, key, ad_account_id):
    state = client.post(
        f"{API}/clients/{cid}/integrations/{key}/oauth/start", headers=admin_headers
    ).json()["state"]
    resp = client.post(
        f"{API}/clients/{cid}/integrations/{key}/oauth/complete",
        headers=admin_headers,
        json={"code": "g-auth-code", "state": state, "ad_account_id": ad_account_id},
    )
    assert resp.status_code == 200, resp.text
    return resp


def test_connect_syncs_ga4_breakdowns(
    client, admin_headers: dict, db_session: Session, google_configured, fake_ga4_full
):
    cid = _client_id(client, admin_headers)
    _connect(client, admin_headers, cid, "ga4", "531798814")

    client_uuid = uuid.UUID(cid)
    rows = db_session.scalars(
        select(AnalyticsBreakdown).where(
            AnalyticsBreakdown.client_id == client_uuid,
            AnalyticsBreakdown.integration_key == "ga4",
        )
    ).all()
    by_type = {}
    for r in rows:
        by_type.setdefault(r.breakdown_type, []).append(r)

    assert [r.dimension for r in sorted(by_type["top_page"], key=lambda r: r.rank)] == [
        "/",
        "/pricing",
    ]
    assert by_type["top_page"][0].metrics["sessions"] in (120, 40)
    assert by_type["channel"][0].dimension == "Organic Search"
    assert by_type["device"][0].dimension == "mobile"

    listed = client.get(
        f"{API}/clients/{cid}/analytics-breakdowns/ga4", headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 4  # 2 top_page + 1 channel + 1 device

    filtered = client.get(
        f"{API}/clients/{cid}/analytics-breakdowns/ga4?type=top_page", headers=admin_headers
    )
    assert filtered.json()["total"] == 2


def test_connect_syncs_search_console_breakdowns(
    client, admin_headers: dict, db_session: Session, google_configured, fake_search_console_full
):
    cid = _client_id(client, admin_headers)
    _connect(
        client, admin_headers, cid, "search_console", "https://tonysgarageinc.com/"
    )

    client_uuid = uuid.UUID(cid)
    rows = db_session.scalars(
        select(AnalyticsBreakdown).where(
            AnalyticsBreakdown.client_id == client_uuid,
            AnalyticsBreakdown.integration_key == "search_console",
        )
    ).all()
    by_type = {r.breakdown_type: r for r in rows}

    assert by_type["top_query"].dimension == "roofing near me"
    assert by_type["top_query"].metrics["clicks"] == 12
    assert by_type["top_page"].dimension == "https://tonysgarageinc.com/"
    assert by_type["device"].dimension == "MOBILE"

    listed = client.get(
        f"{API}/clients/{cid}/analytics-breakdowns/search_console", headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 3


def test_resync_replaces_not_duplicates(
    client, admin_headers: dict, db_session: Session, google_configured, fake_ga4_full
):
    cid = _client_id(client, admin_headers)
    _connect(client, admin_headers, cid, "ga4", "531798814")
    resp = client.post(f"{API}/clients/{cid}/integrations/ga4/sync", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    client_uuid = uuid.UUID(cid)
    rows = db_session.scalars(
        select(AnalyticsBreakdown).where(AnalyticsBreakdown.client_id == client_uuid)
    ).all()
    assert len(rows) == 4  # re-synced in place, not duplicated
