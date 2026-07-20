"""API tests: client assignments (admin only)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201
    return resp.json()["client"]["id"]


def test_admin_assigns_client_to_user(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user()
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == user["email"]


def test_duplicate_assignment_409(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    dup = client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    assert dup.status_code == 409


def test_list_assignments(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    resp = client.get(f"{API}/clients/{cid}/assignments", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_unassign(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    resp = client.delete(f"{API}/clients/{cid}/assignments/{user['id']}", headers=admin_headers)
    assert resp.status_code == 204
    assert (
        client.get(f"{API}/clients/{cid}/assignments", headers=admin_headers).json()["total"] == 0
    )


def test_unassign_unknown_404(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user()
    cid = _client_id(client, admin_headers)
    resp = client.delete(f"{API}/clients/{cid}/assignments/{user['id']}", headers=admin_headers)
    assert resp.status_code == 404


def test_assign_unknown_user_404(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/assignments",
        headers=admin_headers,
        json={"user_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 404


def test_assign_unknown_client_404(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user()
    resp = client.post(
        f"{API}/clients/00000000-0000-0000-0000-000000000000/assignments",
        headers=admin_headers,
        json={"user_id": user["id"]},
    )
    assert resp.status_code == 404


def test_non_admin_cannot_assign(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/assignments", headers=user_headers, json={"user_id": user["id"]}
    )
    assert resp.status_code == 403
