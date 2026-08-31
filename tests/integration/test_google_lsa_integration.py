"""API test: Google LSA account discovery through a manager (MCC) account.

LSA rides the same underlying Google Ads API as ``google_ads`` (shared OAuth
flow, ``_GOOGLE_KEYS``), so this only exercises the one LSA-specific gap that
was just fixed: ``listAccessibleCustomers`` doesn't return a manager account's
*linked* clients — an LSA account we were invited into via the Google Ads UI
(not user-level access) needs the ``customer_client`` expansion to be
selectable at all. Full OAuth-flow mechanics (state, token encryption,
never-auto-bind) are already covered by ``test_google_ads_integration.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.google.lsa import LsaClient
from app.integrations.google.oauth import GoogleOAuthClient
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
    monkeypatch.setattr(s, "google_developer_token", "devtoken123")
    return s


def test_manager_linked_lsa_account_is_discoverable(
    client, admin_headers: dict, google_configured, monkeypatch, db_session: Session
):
    async def exchange_code(self, code):
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_accessible_customers(self, token):
        return ["7516589748"]  # our own manager account only

    async def list_customer_clients(self, token, manager_customer_id):
        assert manager_customer_id == "7516589748"
        return [{"id": "2336702039", "name": "Tampa Bay LSA", "manager": False}]

    async def no_insights_yet(self, access_token, customer_id, *, days=90):
        return []

    async def no_hierarchy(self, access_token, customer_id):
        return {"campaigns": [], "ad_groups": [], "ads": []}

    async def no_campaign_metrics(self, access_token, customer_id, *, days=90):
        return []

    async def no_recommendations(self, access_token, customer_id):
        return []

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(LsaClient, "list_accessible_customers", list_accessible_customers)
    monkeypatch.setattr(LsaClient, "list_customer_clients", list_customer_clients)
    monkeypatch.setattr(LsaClient, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(LsaClient, "fetch_campaign_hierarchy", no_hierarchy)
    monkeypatch.setattr(LsaClient, "fetch_campaign_metrics_daily", no_campaign_metrics)
    monkeypatch.setattr(LsaClient, "fetch_recommendations", no_recommendations)

    cid = _client_id(client, admin_headers)
    state = client.post(
        f"{API}/clients/{cid}/integrations/google_lsa/oauth/start", headers=admin_headers
    ).json()["state"]
    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_lsa/oauth/complete",
        headers=admin_headers,
        json={"code": "g-auth-code", "state": state},
    )
    assert resp.status_code == 200, resp.text
    ids = {a["id"] for a in resp.json()["available_accounts"]}
    assert ids == {"7516589748", "2336702039"}

    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_lsa/oauth/select-account",
        headers=admin_headers,
        json={"ad_account_id": "2336702039"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_account_id"] == "2336702039"
    # LSA never sends login-customer-id when *querying* the bound account,
    # even one discovered through a manager — confirmed at the model level.
    assert resp.json()["login_customer_id"] is None
