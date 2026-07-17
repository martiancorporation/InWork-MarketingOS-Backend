"""API tests: per-client integrations (simulated-OAuth connect/disconnect + RBAC)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload

ALL_KEYS = {"ga4", "search_console", "google_ads", "google_lsa", "meta", "linkedin"}


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def test_list_returns_full_catalog_disconnected(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.get(f"{API}/clients/{cid}/integrations", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # every catalog connector is present, all disconnected by default
    assert {i["key"] for i in items} == ALL_KEYS
    assert all(i["status"] == "disconnected" for i in items)
    assert all(i["account_label"] is None for i in items)


def test_connect_sets_connected_fields(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ga4/connect",
        headers=admin_headers,
        json={
            "account_label": "Acme GA4 — 12345",
            "external_account_id": "properties/12345",
            "scopes": "analytics.readonly",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "ga4"
    assert body["status"] == "connected"
    assert body["account_label"] == "Acme GA4 — 12345"
    assert body["external_account_id"] == "properties/12345"
    assert body["scopes"] == "analytics.readonly"
    assert body["last_sync_at"] is not None
    assert body["last_error"] is None


def test_connect_with_empty_body(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/meta/connect", headers=admin_headers, json={}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert body["account_label"] is None
    assert body["last_sync_at"] is not None


def test_get_reflects_connected_state(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/integrations/linkedin/connect",
        headers=admin_headers,
        json={"account_label": "Acme LinkedIn"},
    )
    resp = client.get(f"{API}/clients/{cid}/integrations/linkedin", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert body["account_label"] == "Acme LinkedIn"
    # list now reflects the connected connector too
    listed = client.get(f"{API}/clients/{cid}/integrations", headers=admin_headers).json()
    linkedin = next(i for i in listed["items"] if i["key"] == "linkedin")
    assert linkedin["status"] == "connected"


def test_get_unconfigured_integration_404(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    # never configured → no stored row → 404 (list is where the catalog default lives)
    resp = client.get(f"{API}/clients/{cid}/integrations/google_ads", headers=admin_headers)
    assert resp.status_code == 404


def test_disconnect_resets_state(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/integrations/ga4/connect",
        headers=admin_headers,
        json={"account_label": "Acme GA4", "scopes": "analytics.readonly"},
    )
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ga4/disconnect", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "disconnected"
    # account_label is kept so the UI can show what it was bound to
    assert body["account_label"] == "Acme GA4"


def test_disconnect_unconfigured_404(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/meta/disconnect", headers=admin_headers
    )
    assert resp.status_code == 404


def test_connect_is_idempotent(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    first = client.post(
        f"{API}/clients/{cid}/integrations/ga4/connect",
        headers=admin_headers,
        json={"account_label": "First"},
    ).json()
    second = client.post(
        f"{API}/clients/{cid}/integrations/ga4/connect",
        headers=admin_headers,
        json={"account_label": "Second"},
    ).json()
    # same row updated in place, no duplicate
    assert first["id"] == second["id"]
    assert second["account_label"] == "Second"
    # catalog still has exactly one ga4 entry
    items = client.get(f"{API}/clients/{cid}/integrations", headers=admin_headers).json()["items"]
    assert len([i for i in items if i["key"] == "ga4"]) == 1


def test_integrations_are_client_scoped(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    # an unassigned user can't tell the client (or its integrations) exists
    assert client.get(f"{API}/clients/{cid}/integrations", headers=user_headers).status_code == 404
    assert (
        client.post(
            f"{API}/clients/{cid}/integrations/ga4/connect", headers=user_headers, json={}
        ).status_code
        == 404
    )


def test_integrations_require_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/integrations").status_code == 401
