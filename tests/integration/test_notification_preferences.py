"""API tests: per-user notification email/mute preferences."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API


def test_get_defaults_to_email_disabled_no_mutes(client: TestClient, admin_headers: dict):
    resp = client.get(f"{API}/notifications/preferences", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email_enabled"] is False
    assert body["muted_client_ids"] == []


def test_set_email_enabled(client: TestClient, admin_headers: dict):
    resp = client.put(
        f"{API}/notifications/preferences", headers=admin_headers, json={"email_enabled": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_enabled"] is True
    # Persists across requests.
    again = client.get(f"{API}/notifications/preferences", headers=admin_headers)
    assert again.json()["email_enabled"] is True


def test_set_muted_clients(client: TestClient, admin_headers: dict):
    cid = "11111111-1111-1111-1111-111111111111"
    resp = client.put(
        f"{API}/notifications/preferences",
        headers=admin_headers,
        json={"muted_client_ids": [cid]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["muted_client_ids"] == [cid]


def test_partial_update_does_not_clear_other_field(client: TestClient, admin_headers: dict):
    client.put(f"{API}/notifications/preferences", headers=admin_headers, json={"email_enabled": True})
    resp = client.put(
        f"{API}/notifications/preferences",
        headers=admin_headers,
        json={"muted_client_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_enabled"] is True


def test_unknown_field_rejected(client: TestClient, admin_headers: dict):
    resp = client.put(
        f"{API}/notifications/preferences", headers=admin_headers, json={"nope": True}
    )
    assert resp.status_code == 422


def test_preferences_are_per_user(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    client.put(f"{API}/notifications/preferences", headers=admin_headers, json={"email_enabled": True})
    mine = client.get(f"{API}/notifications/preferences", headers=user_headers).json()
    assert mine["email_enabled"] is False


def test_preferences_require_auth(client: TestClient):
    assert client.get(f"{API}/notifications/preferences").status_code == 401
    assert client.put(f"{API}/notifications/preferences", json={}).status_code == 401
