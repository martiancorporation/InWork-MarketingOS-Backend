"""API tests: campaigns (CRUD, derived metrics, A/B compare, health) + the
calendar's new CTA and campaign-link fields + RBAC."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _create_campaign(client, headers, cid, **overrides):
    payload = {
        "name": "Black Friday push",
        "objective": "leads",
        "status": "active",
        "budget_usd": 1000,
        "target_cpl": 25,
        "target_ctr": 1.5,
        "target_conversion_rate": 5,
    }
    payload.update(overrides)
    resp = client.post(f"{API}/clients/{cid}/campaigns", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ingest_metrics(client, headers, cid, camp_id, **metrics):
    resp = client.patch(f"{API}/clients/{cid}/campaigns/{camp_id}", headers=headers, json=metrics)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_campaign_defaults(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    body = _create_campaign(client, admin_headers, cid)
    assert body["name"] == "Black Friday push"
    assert body["status"] == "active"
    # No actuals yet → derived metrics are undefined (None), not fabricated.
    assert body["ctr"] is None
    assert body["cpl"] is None
    assert body["roas"] is None


def test_derived_metrics_from_actuals(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _create_campaign(client, admin_headers, cid)
    body = _ingest_metrics(
        client,
        admin_headers,
        cid,
        camp["id"],
        impressions=1000,
        clicks=20,
        conversions=2,
        leads=5,
        spend=100,
        revenue=400,
    )
    assert body["ctr"] == 2.0  # 20/1000
    assert body["cpl"] == 20.0  # 100/5
    assert body["conversion_rate"] == 10.0  # 2/20
    assert body["roas"] == 4.0  # 400/100


def test_list_with_status_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create_campaign(client, admin_headers, cid, name="Live one", status="active")
    _create_campaign(client, admin_headers, cid, name="Old one", status="ended")
    resp = client.get(f"{API}/clients/{cid}/campaigns?status=active", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Live one"


def test_compare_picks_winners(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    a = _create_campaign(client, admin_headers, cid, name="A")
    b = _create_campaign(client, admin_headers, cid, name="B")
    # A: better CTR; B: better (lower) CPL.
    _ingest_metrics(
        client, admin_headers, cid, a["id"], impressions=1000, clicks=50, leads=2, spend=100
    )
    _ingest_metrics(
        client, admin_headers, cid, b["id"], impressions=1000, clicks=10, leads=10, spend=100
    )
    resp = client.get(
        f"{API}/clients/{cid}/campaigns/compare?ids={a['id']}&ids={b['id']}",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["rows"]) == 2
    assert data["winners"]["ctr"] == a["id"]  # 5% vs 1%
    assert data["winners"]["cpl"] == b["id"]  # $10 vs $50


def test_compare_requires_two_ids(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    a = _create_campaign(client, admin_headers, cid)
    resp = client.get(f"{API}/clients/{cid}/campaigns/compare?ids={a['id']}", headers=admin_headers)
    assert resp.status_code == 422  # min 2 ids


def test_health_meets_targets(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _create_campaign(client, admin_headers, cid)  # cpl<=25, ctr>=1.5, cvr>=5
    _ingest_metrics(
        client,
        admin_headers,
        cid,
        camp["id"],
        impressions=1000,
        clicks=20,
        conversions=2,
        leads=5,
        spend=100,
    )
    resp = client.get(f"{API}/clients/{cid}/campaigns/{camp['id']}/health", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_targets"] is True
    assert data["score"] >= 85 and data["band"] == "excellent"
    assert len(data["drivers"]) == 3


def test_health_without_targets(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _create_campaign(
        client,
        admin_headers,
        cid,
        target_cpl=None,
        target_ctr=None,
        target_conversion_rate=None,
    )
    resp = client.get(f"{API}/clients/{cid}/campaigns/{camp['id']}/health", headers=admin_headers)
    data = resp.json()
    assert data["has_targets"] is False
    assert data["band"] == "attention"


def test_event_links_to_campaign_and_post_cta(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _create_campaign(client, admin_headers, cid)
    event_body = {
        "title": "Launch post",
        "type": "campaign",
        "platform": "instagram",
        "event_date": "2026-07-15",
        "event_time": "09:00:00",
        "campaign_id": camp["id"],
        "post": {
            "caption": "Big day!",
            "hashtags": "#launch",
            "cta_label": "Book Now",
            "cta_url": "https://acme.com/book",
        },
    }
    resp = client.post(
        f"{API}/clients/{cid}/calendar/events", headers=admin_headers, json=event_body
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["campaign_id"] == camp["id"]
    assert body["post"]["cta_label"] == "Book Now"
    assert body["post"]["cta_url"] == "https://acme.com/book"


def test_delete_campaign(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _create_campaign(client, admin_headers, cid)
    assert (
        client.delete(
            f"{API}/clients/{cid}/campaigns/{camp['id']}", headers=admin_headers
        ).status_code
        == 200
    )
    assert (
        client.get(f"{API}/clients/{cid}/campaigns/{camp['id']}", headers=admin_headers).status_code
        == 404
    )


def test_bad_status_422(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/campaigns",
        headers=admin_headers,
        json={"name": "X", "status": "not-a-status"},
    )
    assert resp.status_code == 422


def test_scoping_unassigned_user_404(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    _create_campaign(client, admin_headers, cid)
    _user, headers = make_user(email="nobody@test.com")
    assert client.get(f"{API}/clients/{cid}/campaigns", headers=headers).status_code == 404


def test_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/campaigns").status_code == 401
