"""API tests: the per-user notification centre, and the watchdog sweep feeding it."""

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


def _assign(client, admin_headers, cid, user_id):
    resp = client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user_id}
    )
    assert resp.status_code in (200, 201), resp.text


def _breaching_campaign(client, admin_headers, cid):
    camp = client.post(
        f"{API}/clients/{cid}/campaigns",
        headers=admin_headers,
        json={"name": "C", "status": "active", "budget_usd": 100000, "target_cpl": 25},
    ).json()
    client.patch(
        f"{API}/clients/{cid}/campaigns/{camp['id']}",
        headers=admin_headers,
        json={"leads": 2, "spend": 100},
    )


def test_watchdog_notifies_assigned_user(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    user, headers = make_user(email="specialist@test.com")
    _assign(client, admin_headers, cid, user["id"])
    _breaching_campaign(client, admin_headers, cid)

    # Admin runs the platform watchdog sweep → the assigned user gets a notification.
    client.post(f"{API}/automation/watchdog/run", headers=admin_headers)

    assert client.get(f"{API}/notifications/unread-count", headers=headers).json()["unread"] == 1
    listing = client.get(f"{API}/notifications", headers=headers).json()
    assert listing["total"] == 1
    n = listing["items"][0]
    assert n["kind"] == "alert" and n["level"] == "warning"
    assert n["link"] == f"/clients/{cid}/alerts"

    # Mark it read → the badge clears.
    client.post(f"{API}/notifications/{n['id']}/read", headers=headers)
    assert client.get(f"{API}/notifications/unread-count", headers=headers).json()["unread"] == 0


def test_watchdog_notifications_dedup(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    user, headers = make_user(email="dedup@test.com")
    _assign(client, admin_headers, cid, user["id"])
    _breaching_campaign(client, admin_headers, cid)
    client.post(f"{API}/automation/watchdog/run", headers=admin_headers)
    client.post(f"{API}/automation/watchdog/run", headers=admin_headers)  # second sweep
    # rec_key dedup → still one notification, not two.
    assert client.get(f"{API}/notifications", headers=headers).json()["total"] == 1


def test_notifications_are_per_user(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    a_user, a_headers = make_user(email="a@test.com")
    _b_user, b_headers = make_user(email="b@test.com")
    _assign(client, admin_headers, cid, a_user["id"])  # only A is assigned
    _breaching_campaign(client, admin_headers, cid)
    client.post(f"{API}/automation/watchdog/run", headers=admin_headers)
    assert client.get(f"{API}/notifications", headers=a_headers).json()["total"] == 1
    assert client.get(f"{API}/notifications", headers=b_headers).json()["total"] == 0


def test_mark_all_read(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    user, headers = make_user(email="markall@test.com")
    _assign(client, admin_headers, cid, user["id"])
    _breaching_campaign(client, admin_headers, cid)
    client.post(f"{API}/automation/watchdog/run", headers=admin_headers)
    assert client.post(f"{API}/notifications/read-all", headers=headers).status_code == 200
    assert client.get(f"{API}/notifications/unread-count", headers=headers).json()["unread"] == 0


def test_notifications_require_auth(client: TestClient, admin_headers: dict):
    assert client.get(f"{API}/notifications").status_code == 401
    assert client.get(f"{API}/notifications/unread-count").status_code == 401
