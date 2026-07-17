"""API tests: REAL Google Ads OAuth2 integration (start → complete → sync),
including the refresh-token path. Google's network is faked; the full flow —
signed state, code exchange, encrypted access+refresh storage, token refresh on
expiry, and metrics → analytics — is exercised. Config enabled per-test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.crypto import TokenCipher
from app.integrations.google.ads import GoogleAdsClient
from app.integrations.google.oauth import GoogleOAuthClient
from app.models.analytics import AnalyticsDaily
from app.models.enums import SocialPlatform
from app.models.integration import Integration
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


@pytest.fixture
def fake_google(monkeypatch):
    async def exchange_code(self, code):
        assert code == "g-auth-code"
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_accessible_customers(self, token):
        assert token == "g-access"
        return ["1234567890"]

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(GoogleAdsClient, "list_accessible_customers", list_accessible_customers)


def _connect(client, admin_headers, cid):
    start = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/start", headers=admin_headers
    ).json()
    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/complete",
        headers=admin_headers,
        json={"code": "g-auth-code", "state": start["state"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_oauth_start_unconfigured_503(client, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/start", headers=admin_headers
    )
    assert resp.status_code == 503


def test_oauth_start_url_is_google(client, admin_headers: dict, google_configured):
    cid = _client_id(client, admin_headers)
    body = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/start", headers=admin_headers
    ).json()
    url = body["authorization_url"]
    assert "accounts.google.com" in url
    assert "access_type=offline" in url  # asks for a refresh token
    assert "adwords" in url  # the Google Ads scope


def test_full_flow_stores_access_and_refresh_encrypted(
    client, admin_headers: dict, db_session: Session, google_configured, fake_google
):
    cid = _client_id(client, admin_headers)
    body = _connect(client, admin_headers, cid)
    assert body["status"] == "connected"
    assert body["external_account_id"] == "1234567890"

    row = db_session.scalar(
        select(Integration).where(Integration.client_id == uuid.UUID(cid))
    )
    cipher = TokenCipher()
    assert cipher.decrypt(row.access_token_encrypted) == "g-access"
    assert row.refresh_token_encrypted is not None
    assert cipher.decrypt(row.refresh_token_encrypted) == "g-refresh"  # refresh token stored


def test_sync_pulls_metrics_into_analytics(
    client, admin_headers: dict, db_session: Session, google_configured, fake_google, monkeypatch
):
    cid = _client_id(client, admin_headers)
    _connect(client, admin_headers, cid)

    async def fake_metrics(self, access_token, customer_id):
        assert access_token == "g-access" and customer_id == "1234567890"
        return {"impressions": 5000, "clicks": 120, "spend": 340.5,
                "conversions": 18, "leads": 18, "revenue": 1500.0}

    monkeypatch.setattr(GoogleAdsClient, "fetch_metrics", fake_metrics)
    resp = client.post(f"{API}/clients/{cid}/integrations/google_ads/sync", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    row = db_session.scalar(
        select(AnalyticsDaily).where(
            AnalyticsDaily.client_id == uuid.UUID(cid),
            AnalyticsDaily.platform == SocialPlatform.google,
        )
    )
    assert row is not None
    assert row.impressions == 5000 and row.clicks == 120 and float(row.spend) == 340.5


def test_sync_refreshes_expired_token(
    client, admin_headers: dict, db_session: Session, google_configured, fake_google, monkeypatch
):
    cid = _client_id(client, admin_headers)
    _connect(client, admin_headers, cid)

    # Force the stored access token to look expired.
    row = db_session.scalar(select(Integration).where(Integration.client_id == uuid.UUID(cid)))
    row.token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()

    async def fake_refresh(self, refresh_token):
        assert refresh_token == "g-refresh"
        return {"access_token": "g-access-REFRESHED", "expires_in": 3600}

    async def fake_metrics(self, access_token, customer_id):
        assert access_token == "g-access-REFRESHED"  # the refreshed token was used
        return {"impressions": 1, "clicks": 1, "spend": 1.0,
                "conversions": 0, "leads": 0, "revenue": 0.0}

    monkeypatch.setattr(GoogleOAuthClient, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(GoogleAdsClient, "fetch_metrics", fake_metrics)
    resp = client.post(f"{API}/clients/{cid}/integrations/google_ads/sync", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    row = db_session.scalar(select(Integration).where(Integration.client_id == uuid.UUID(cid)))
    assert TokenCipher().decrypt(row.access_token_encrypted) == "g-access-REFRESHED"


def test_bad_state_rejected(client, admin_headers: dict, google_configured, fake_google):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/complete",
        headers=admin_headers,
        json={"code": "g-auth-code", "state": "forged"},
    )
    assert resp.status_code == 400
