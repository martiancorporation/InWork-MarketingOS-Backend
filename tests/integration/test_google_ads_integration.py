"""API tests: REAL Google Ads OAuth2 integration (start → complete →
[select-account] → sync), including the refresh-token path. Google's network
is faked; the full flow — signed state, code exchange, encrypted
access+refresh storage, the never-auto-bind account picker (same contract as
Meta), and metrics → analytics — is exercised. Config enabled per-test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

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
    """A single accessible customer — still requires an explicit
    ``ad_account_id`` (or a follow-up ``select-account``) to bind, per the
    never-auto-bind contract shared with Meta."""

    async def exchange_code(self, code):
        assert code == "g-auth-code"
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_accessible_customers(self, token):
        assert token == "g-access"
        return ["1234567890"]

    async def no_linked_clients(self, token, manager_customer_id):
        # Discovery also expands each accessible account through
        # customer_client (manager-linked accounts) — none in this fixture.
        return []

    async def no_insights_yet(self, access_token, customer_id, *, login_customer_id=None, days=90):
        # oauth/complete auto-syncs immediately once an account is bound —
        # default to "nothing yet" so connect-only tests stay hermetic (no
        # real network call); tests that care about sync results override
        # this themselves.
        return []

    async def no_hierarchy(self, access_token, customer_id, *, login_customer_id=None):
        return {"campaigns": [], "ad_groups": [], "ads": []}

    async def no_campaign_metrics(
        self, access_token, customer_id, *, login_customer_id=None, days=90
    ):
        return []

    async def no_recommendations(self, access_token, customer_id, *, login_customer_id=None):
        return []

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(GoogleAdsClient, "list_accessible_customers", list_accessible_customers)
    monkeypatch.setattr(GoogleAdsClient, "list_customer_clients", no_linked_clients)
    monkeypatch.setattr(GoogleAdsClient, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_hierarchy", no_hierarchy)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_metrics_daily", no_campaign_metrics)
    monkeypatch.setattr(GoogleAdsClient, "fetch_recommendations", no_recommendations)


@pytest.fixture
def fake_google_multi(monkeypatch):
    """Two accessible customers — the ambiguous case."""

    async def exchange_code(self, code):
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_accessible_customers(self, token):
        return ["1111111111", "2222222222"]

    async def no_linked_clients(self, token, manager_customer_id):
        return []

    async def no_insights_yet(self, access_token, customer_id, *, login_customer_id=None, days=90):
        return []

    async def no_hierarchy(self, access_token, customer_id, *, login_customer_id=None):
        return {"campaigns": [], "ad_groups": [], "ads": []}

    async def no_campaign_metrics(
        self, access_token, customer_id, *, login_customer_id=None, days=90
    ):
        return []

    async def no_recommendations(self, access_token, customer_id, *, login_customer_id=None):
        return []

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(GoogleAdsClient, "list_accessible_customers", list_accessible_customers)
    monkeypatch.setattr(GoogleAdsClient, "list_customer_clients", no_linked_clients)
    monkeypatch.setattr(GoogleAdsClient, "fetch_daily_insights", no_insights_yet)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_hierarchy", no_hierarchy)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_metrics_daily", no_campaign_metrics)
    monkeypatch.setattr(GoogleAdsClient, "fetch_recommendations", no_recommendations)


def _state(client, admin_headers, cid):
    return client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/start", headers=admin_headers
    ).json()["state"]


def _complete(client, admin_headers, cid, **extra):
    return client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/complete",
        headers=admin_headers,
        json={"code": "g-auth-code", "state": _state(client, admin_headers, cid), **extra},
    )


def _connect(client, admin_headers, cid, ad_account_id="1234567890"):
    """Pins the account directly at complete time — for tests that are about
    something other than the selection flow itself."""
    resp = _complete(client, admin_headers, cid, ad_account_id=ad_account_id)
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

    row = db_session.scalar(select(Integration).where(Integration.client_id == uuid.UUID(cid)))
    cipher = TokenCipher()
    assert cipher.decrypt(row.access_token_encrypted) == "g-access"
    assert row.refresh_token_encrypted is not None
    assert cipher.decrypt(row.refresh_token_encrypted) == "g-refresh"  # refresh token stored


def test_single_account_still_requires_explicit_pick(
    client, admin_headers: dict, google_configured, fake_google
):
    """Even with exactly one accessible customer, oauth/complete must not
    auto-bind — same contract as Meta."""
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid)  # no ad_account_id -> ambiguous
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert body["external_account_id"] is None
    assert body["available_accounts"] == [{"id": "1234567890", "name": "1234567890"}]


def test_operator_supplied_login_customer_id_is_stored_and_used(
    client, admin_headers: dict, db_session: Session, google_configured, fake_google, monkeypatch
):
    """The MCC id for a manager-linked sub-account is entered by the operator
    at connect time (not hardcoded) and threaded into every later sync call."""
    cid = _client_id(client, admin_headers)
    resp = _complete(
        client,
        admin_headers,
        cid,
        ad_account_id="1234567890",
        login_customer_id="452-764-8021",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["login_customer_id"] == "452-764-8021"

    row = db_session.scalar(select(Integration).where(Integration.client_id == uuid.UUID(cid)))
    assert row.login_customer_id == "452-764-8021"

    seen = {}

    async def fake_daily_insights(
        self, access_token, customer_id, *, login_customer_id=None, days=90
    ):
        seen["login_customer_id"] = login_customer_id
        return []

    monkeypatch.setattr(GoogleAdsClient, "fetch_daily_insights", fake_daily_insights)
    sync_resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/sync", headers=admin_headers
    )
    assert sync_resp.status_code == 200, sync_resp.text
    assert seen["login_customer_id"] == "452-764-8021"


def test_sync_pulls_metrics_into_analytics(
    client, admin_headers: dict, db_session: Session, google_configured, fake_google, monkeypatch
):
    cid = _client_id(client, admin_headers)
    _connect(client, admin_headers, cid)

    async def fake_daily_insights(
        self, access_token, customer_id, *, login_customer_id=None, days=90
    ):
        assert access_token == "g-access" and customer_id == "1234567890"
        return [
            {
                "date": date.today(),
                "impressions": 5000,
                "clicks": 120,
                "spend": 340.5,
                "conversions": 18,
                "leads": 18,
                "revenue": 1500.0,
            }
        ]

    monkeypatch.setattr(GoogleAdsClient, "fetch_daily_insights", fake_daily_insights)
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

    async def fake_daily_insights(
        self, access_token, customer_id, *, login_customer_id=None, days=90
    ):
        assert access_token == "g-access-REFRESHED"  # the refreshed token was used
        return [
            {
                "date": date.today(),
                "impressions": 1,
                "clicks": 1,
                "spend": 1.0,
                "conversions": 0,
                "leads": 0,
                "revenue": 0.0,
            }
        ]

    monkeypatch.setattr(GoogleOAuthClient, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(GoogleAdsClient, "fetch_daily_insights", fake_daily_insights)
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


def test_sync_requires_account_selection_first(
    client, admin_headers: dict, google_configured, fake_google
):
    cid = _client_id(client, admin_headers)
    _complete(client, admin_headers, cid)  # ambiguous — never finished with select-account
    resp = client.post(f"{API}/clients/{cid}/integrations/google_ads/sync", headers=admin_headers)
    assert resp.status_code == 400
    assert "account is bound" in resp.json()["error"]["message"]


# ---- ad-account selection when the authorized user has several ----


def test_multiple_accounts_connect_without_binding_one(
    client, admin_headers: dict, google_configured, fake_google_multi
):
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid)  # no ad_account_id -> ambiguous
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert body["external_account_id"] is None
    assert body["available_accounts"] == [
        {"id": "1111111111", "name": "1111111111"},
        {"id": "2222222222", "name": "2222222222"},
    ]


def test_select_account_binds_the_chosen_one(
    client, admin_headers: dict, google_configured, fake_google_multi
):
    cid = _client_id(client, admin_headers)
    _complete(client, admin_headers, cid)  # connects, unbound (ambiguous)

    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/select-account",
        headers=admin_headers,
        json={"ad_account_id": "2222222222", "login_customer_id": "452-764-8021"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_account_id"] == "2222222222"
    assert resp.json()["login_customer_id"] == "452-764-8021"


def test_manager_linked_client_account_is_discoverable(
    client, admin_headers: dict, google_configured, monkeypatch
):
    """``listAccessibleCustomers`` only returns accounts the OAuth user has
    direct access to — a client account linked under that manager via the
    Google Ads UI (not user-level access) must still show up in
    ``available_accounts`` via the ``customer_client`` expansion."""

    async def exchange_code(self, code):
        return {"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3600}

    async def list_accessible_customers(self, token):
        return ["7516589748"]  # our own manager account only

    async def list_customer_clients(self, token, manager_customer_id):
        assert manager_customer_id == "7516589748"
        return [{"id": "2935574193", "name": "Family First Roofing of Florida", "manager": False}]

    async def no_insights_yet(self, access_token, customer_id, *, login_customer_id=None, days=90):
        return []

    async def no_hierarchy(self, access_token, customer_id, *, login_customer_id=None):
        return {"campaigns": [], "ad_groups": [], "ads": []}

    async def no_campaign_metrics(
        self, access_token, customer_id, *, login_customer_id=None, days=90
    ):
        return []

    async def no_recommendations(self, access_token, customer_id, *, login_customer_id=None):
        return []

    monkeypatch.setattr(GoogleOAuthClient, "exchange_code", exchange_code)
    monkeypatch.setattr(GoogleAdsClient, "list_accessible_customers", list_accessible_customers)
    monkeypatch.setattr(GoogleAdsClient, "list_customer_clients", list_customer_clients)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_hierarchy", no_hierarchy)
    monkeypatch.setattr(GoogleAdsClient, "fetch_campaign_metrics_daily", no_campaign_metrics)
    monkeypatch.setattr(GoogleAdsClient, "fetch_recommendations", no_recommendations)
    monkeypatch.setattr(GoogleAdsClient, "fetch_daily_insights", no_insights_yet)

    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid)
    assert resp.status_code == 200, resp.text
    ids = {a["id"] for a in resp.json()["available_accounts"]}
    assert ids == {"7516589748", "2935574193"}

    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/select-account",
        headers=admin_headers,
        json={"ad_account_id": "2935574193", "login_customer_id": "751-658-9748"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_account_id"] == "2935574193"


def test_select_account_rejects_unknown_id(
    client, admin_headers: dict, google_configured, fake_google_multi
):
    cid = _client_id(client, admin_headers)
    _complete(client, admin_headers, cid)

    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/select-account",
        headers=admin_headers,
        json={"ad_account_id": "9999999999"},
    )
    assert resp.status_code == 400
    assert "isn't accessible" in resp.json()["error"]["message"]


def test_select_account_requires_connected_integration(
    client, admin_headers: dict, google_configured
):
    cid = _client_id(client, admin_headers)  # never ran OAuth
    resp = client.post(
        f"{API}/clients/{cid}/integrations/google_ads/oauth/select-account",
        headers=admin_headers,
        json={"ad_account_id": "1234567890"},
    )
    assert resp.status_code in (400, 404)


def test_ad_account_id_selects_the_right_one(
    client, admin_headers: dict, google_configured, fake_google_multi
):
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid, ad_account_id="2222222222")
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_account_id"] == "2222222222"


def test_unknown_ad_account_id_is_rejected(
    client, admin_headers: dict, google_configured, fake_google_multi
):
    cid = _client_id(client, admin_headers)
    resp = _complete(client, admin_headers, cid, ad_account_id="9999999999")
    assert resp.status_code == 400
    assert "isn't accessible" in resp.json()["error"]["message"]
