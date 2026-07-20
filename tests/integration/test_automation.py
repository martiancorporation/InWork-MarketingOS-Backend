"""API tests: platform automation (/automation) — watchdog sweep, integration
sync sweep, and daily digests, plus admin-only enforcement."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.integrations.crypto import TokenCipher
from app.integrations.meta.client import MetaClient
from app.models.enums import IntegrationKey, IntegrationStatus
from app.models.integration import Integration
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _breaching_campaign(client, admin_headers, cid):
    camp = client.post(
        f"{API}/clients/{cid}/campaigns",
        headers=admin_headers,
        json={"name": "C", "status": "active", "budget_usd": 100000, "target_cpl": 25},
    ).json()
    # cpl = $50 (100% over the $25 target) → a high-severity alert
    client.patch(
        f"{API}/clients/{cid}/campaigns/{camp['id']}",
        headers=admin_headers,
        json={"leads": 2, "spend": 100},
    )
    return camp["id"]


def test_watchdog_sweep_across_clients(client, admin_headers: dict):
    a = _client_id(client, admin_headers, "Client A")
    b = _client_id(client, admin_headers, "Client B")
    _breaching_campaign(client, admin_headers, a)
    _breaching_campaign(client, admin_headers, b)
    resp = client.post(f"{API}/automation/watchdog/run", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["clients"] >= 2
    assert data["opened"] >= 2
    assert any(r["opened"] >= 1 for r in data["per_client"])


def test_integration_sync_sweep_syncs_connected(
    client, admin_headers: dict, db_session: Session, monkeypatch
):
    cid = _client_id(client, admin_headers)
    # A connected Meta integration with an encrypted token (as OAuth would leave it).
    db_session.add(
        Integration(
            client_id=uuid.UUID(cid),
            key=IntegrationKey.meta,
            status=IntegrationStatus.connected,
            external_account_id="act_1",
            access_token_encrypted=TokenCipher().encrypt("tok"),
        )
    )
    db_session.commit()

    async def fake_insights(self, token, ad_account_id, *, date_preset="last_30d"):
        return {
            "impressions": 100,
            "clicks": 5,
            "spend": 10.0,
            "leads": 1,
            "conversions": 0,
            "revenue": 0.0,
        }

    monkeypatch.setattr(MetaClient, "fetch_insights", fake_insights)
    resp = client.post(f"{API}/automation/integrations/sync", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["synced"] == 1 and data["failed"] == 0
    assert data["details"][0]["ok"] is True and data["details"][0]["key"] == "meta"


def test_integration_sync_sweep_nothing_connected(client, admin_headers: dict):
    _client_id(client, admin_headers)
    resp = client.post(f"{API}/automation/integrations/sync", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["synced"] == 0


def test_client_digest(client, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _breaching_campaign(client, admin_headers, cid)
    client.post(f"{API}/automation/watchdog/run", headers=admin_headers)  # opens an alert
    resp = client.get(f"{API}/automation/clients/{cid}/digest", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["open_alerts"] >= 1
    assert d["high"] >= 1
    assert d["campaign_count"] == 1
    assert "meta" in d["pending_integrations"]  # none connected
    assert d["onboarding_percent"] == 100  # atomic onboarding completes the wizard


def test_all_digests(client, admin_headers: dict):
    _client_id(client, admin_headers, "One")
    _client_id(client, admin_headers, "Two")
    resp = client.get(f"{API}/automation/digest", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 2


def test_automation_is_admin_only(client, admin_headers: dict, make_user):
    _user, headers = make_user(email="specialist@test.com")
    assert client.post(f"{API}/automation/watchdog/run", headers=headers).status_code == 403
    assert client.get(f"{API}/automation/digest", headers=headers).status_code == 403


def test_automation_requires_auth(client, admin_headers: dict):
    assert client.post(f"{API}/automation/watchdog/run").status_code == 401
