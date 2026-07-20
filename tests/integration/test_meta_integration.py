"""API tests: REAL Meta OAuth2 integration (start → complete → sync).

Meta's network calls are faked (no real app in the hermetic suite), but the full
flow is exercised: signed-state round-trip, token exchange, encrypted storage,
and insights → analytics ingestion. Config is enabled per-test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.crypto import TokenCipher
from app.integrations.meta.client import MetaClient
from app.integrations.meta.oauth import MetaOAuthClient
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
def meta_configured(monkeypatch):
    s = get_settings().integrations
    monkeypatch.setattr(s, "meta_app_id", "app123")
    monkeypatch.setattr(s, "meta_app_secret", "secret456")
    monkeypatch.setattr(s, "meta_redirect_uri", "https://app.inwork.com/oauth/meta/callback")
    return s


@pytest.fixture
def fake_meta_oauth(monkeypatch):
    async def exchange_code(self, code):
        assert code == "auth-code-abc"
        return {"access_token": "short-token", "expires_in": 3600}

    async def exchange_long_lived(self, short_token):
        assert short_token == "short-token"
        return {"access_token": "long-lived-token", "expires_in": 5_184_000}

    async def list_ad_accounts(self, token):
        assert token == "long-lived-token"
        return [{"account_id": "act_999", "name": "Acme Ad Account"}]

    monkeypatch.setattr(MetaOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(MetaOAuthClient, "exchange_long_lived", exchange_long_lived)
    monkeypatch.setattr(MetaOAuthClient, "list_ad_accounts", list_ad_accounts)


def test_oauth_start_unconfigured_returns_503(client, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers)
    assert resp.status_code == 503


def test_oauth_start_returns_authorization_url(client, admin_headers: dict, meta_configured):
    cid = _client_id(client, admin_headers)
    resp = client.post(f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "facebook.com" in body["authorization_url"]
    assert "client_id=app123" in body["authorization_url"]
    assert "state=" in body["authorization_url"]  # state is URL-encoded in the URL
    assert body["state"]  # a signed state was issued


def test_full_oauth_stores_encrypted_token(
    client, admin_headers: dict, db_session: Session, meta_configured, fake_meta_oauth
):
    cid = _client_id(client, admin_headers)
    start = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers
    ).json()
    resp = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/complete",
        headers=admin_headers,
        json={"code": "auth-code-abc", "state": start["state"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert body["external_account_id"] == "act_999"
    assert body["account_label"] == "Acme Ad Account"

    # The token is stored ENCRYPTED, and decrypts back to the long-lived token.
    row = db_session.scalar(select(Integration).where(Integration.client_id == uuid.UUID(cid)))
    assert row.access_token_encrypted is not None
    assert row.access_token_encrypted != "long-lived-token"  # not plaintext
    assert TokenCipher().decrypt(row.access_token_encrypted) == "long-lived-token"
    assert row.token_expires_at is not None


def test_complete_rejects_bad_state(client, admin_headers: dict, meta_configured, fake_meta_oauth):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/complete",
        headers=admin_headers,
        json={"code": "auth-code-abc", "state": "forged-state"},
    )
    assert resp.status_code == 400


def test_sync_pulls_insights_into_analytics(
    client, admin_headers: dict, db_session: Session, meta_configured, fake_meta_oauth, monkeypatch
):
    cid = _client_id(client, admin_headers)
    start = client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers
    ).json()
    client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/complete",
        headers=admin_headers,
        json={"code": "auth-code-abc", "state": start["state"]},
    )

    async def fake_insights(self, token, ad_account_id, *, date_preset="last_30d"):
        assert token == "long-lived-token" and ad_account_id == "act_999"
        return {
            "impressions": 1000,
            "clicks": 50,
            "spend": 200.0,
            "leads": 10,
            "conversions": 3,
            "revenue": 900.0,
        }

    monkeypatch.setattr(MetaClient, "fetch_insights", fake_insights)
    resp = client.post(f"{API}/clients/{cid}/integrations/meta/sync", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_sync_at"] is not None

    row = db_session.scalar(
        select(AnalyticsDaily).where(
            AnalyticsDaily.client_id == uuid.UUID(cid),
            AnalyticsDaily.platform == SocialPlatform.facebook,
        )
    )
    assert row is not None
    assert row.impressions == 1000 and row.leads == 10 and float(row.spend) == 200.0


def test_sync_requires_connection(client, admin_headers: dict, meta_configured):
    cid = _client_id(client, admin_headers)
    resp = client.post(f"{API}/clients/{cid}/integrations/meta/sync", headers=admin_headers)
    assert resp.status_code in (400, 404)  # never connected


def test_oauth_unconfigured_returns_503(client, admin_headers: dict, meta_configured):
    cid = _client_id(client, admin_headers)
    resp = client.post(f"{API}/clients/{cid}/integrations/ga4/oauth/start", headers=admin_headers)
    assert resp.status_code == 503  # GA4 real OAuth is supported but unconfigured here


def test_oauth_requires_auth(client, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.post(f"{API}/clients/{cid}/integrations/meta/oauth/start").status_code == 401


# ---- ad-account selection when the authorized user has several ----


@pytest.fixture
def fake_meta_multi(monkeypatch):
    async def exchange_code(self, code):
        return {"access_token": "short-token", "expires_in": 3600}

    async def exchange_long_lived(self, short_token):
        return {"access_token": "long-lived-token", "expires_in": 5_184_000}

    async def list_ad_accounts(self, token):
        return [
            {"account_id": "act_111", "name": "Client Main"},
            {"account_id": "act_222", "name": "Client Secondary"},
        ]

    monkeypatch.setattr(MetaOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(MetaOAuthClient, "exchange_long_lived", exchange_long_lived)
    monkeypatch.setattr(MetaOAuthClient, "list_ad_accounts", list_ad_accounts)


def _state(client, admin_headers, cid):
    return client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/start", headers=admin_headers
    ).json()["state"]


def _complete(client, admin_headers, cid, **extra):
    return client.post(
        f"{API}/clients/{cid}/integrations/meta/oauth/complete",
        headers=admin_headers,
        json={"code": "c", "state": _state(client, admin_headers, cid), **extra},
    )


def test_multiple_accounts_require_ad_account_id(
    client, admin_headers: dict, meta_configured, fake_meta_multi
):
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid)  # no ad_account_id → ambiguous
    assert resp.status_code == 400
    assert "ad_account_id" in resp.json()["error"]["message"]


def test_ad_account_id_selects_the_right_one(
    client, admin_headers: dict, meta_configured, fake_meta_multi
):
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid, ad_account_id="act_222")
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_account_id"] == "act_222"
    assert resp.json()["account_label"] == "Client Secondary"


def test_ad_account_id_matches_without_act_prefix(
    client, admin_headers: dict, meta_configured, fake_meta_multi
):
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid, ad_account_id="111")
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_account_id"] == "act_111"


def test_unknown_ad_account_id_is_rejected(
    client, admin_headers: dict, meta_configured, fake_meta_multi
):
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid, ad_account_id="act_does_not_exist")
    assert resp.status_code == 400
    assert "isn't accessible" in resp.json()["error"]["message"]
