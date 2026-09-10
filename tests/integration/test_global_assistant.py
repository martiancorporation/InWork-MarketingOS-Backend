"""API tests: the platform-wide "Ask AI about my portfolio" assistant
(``POST /assistant/ask``) — specifically its access scoping.

This is the one piece of authorization logic in the codebase that had zero
test coverage: ``GlobalAssistantService._accessible_clients`` decides whether
a caller reasons over every client (admin) or only the ones assigned to them
(everyone else). Get that wrong and a non-admin's question could surface
another client's name/spend/leads data in the answer.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _onboard(client: TestClient, admin_headers: dict, name: str) -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _assign(client: TestClient, admin_headers: dict, client_id: str, user_id: str) -> None:
    resp = client.post(
        f"{API}/clients/{client_id}/assignments", headers=admin_headers, json={"user_id": user_id}
    )
    assert resp.status_code == 201, resp.text


def test_non_admin_sees_only_assigned_clients(
    client: TestClient, admin_headers: dict, make_user
) -> None:
    assigned_id = _onboard(client, admin_headers, "Assigned Client")
    _onboard(client, admin_headers, "Other Client 1")
    _onboard(client, admin_headers, "Other Client 2")

    user_json, user_headers = make_user(email="scoped@test.com")
    _assign(client, admin_headers, assigned_id, user_json["id"])

    resp = client.post(
        f"{API}/assistant/ask", headers=user_headers, json={"content": "How is my portfolio doing?"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clients_considered"] == 1
    assert body["scope"] == "1 assigned client(s)"
    # The unassigned clients' names must never reach this user's answer —
    # the deterministic fallback (AI provider unconfigured in tests) echoes the
    # portfolio fact sheet directly, so this also guards against the fact
    # sheet itself leaking another client's data.
    assert "Other Client 1" not in body["answer"]
    assert "Other Client 2" not in body["answer"]


def test_non_admin_with_no_assignments_sees_nothing(
    client: TestClient, admin_headers: dict, make_user
) -> None:
    _onboard(client, admin_headers, "Unassigned Client")
    _user_json, user_headers = make_user(email="noassign@test.com")

    resp = client.post(
        f"{API}/assistant/ask", headers=user_headers, json={"content": "How is my portfolio doing?"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clients_considered"] == 0
    assert "Unassigned Client" not in body["answer"]


def test_admin_sees_every_client(client: TestClient, admin_headers: dict) -> None:
    _onboard(client, admin_headers, "Admin-Visible Client 1")
    _onboard(client, admin_headers, "Admin-Visible Client 2")

    resp = client.post(
        f"{API}/assistant/ask",
        headers=admin_headers,
        json={"content": "How is my portfolio doing?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clients_considered"] >= 2
    assert body["scope"] == "all clients"
    assert "Admin-Visible Client 1" in body["answer"]
    assert "Admin-Visible Client 2" in body["answer"]


def test_requires_auth(client: TestClient) -> None:
    resp = client.post(f"{API}/assistant/ask", json={"content": "hi"})
    assert resp.status_code == 401
