"""End-to-end process flow: the full RBAC journey in one test.

Mirrors the product spec:
  admin logs in -> admin creates a user -> admin onboards a client ->
  the user is scoped out until assigned -> admin assigns -> user gains access ->
  admin unassigns -> user loses access -> admin always sees everything.

The admin is provisioned the way production does it — seeded, then logged in
(the ``admin_headers`` fixture) — since there is no public sign-up.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_full_rbac_process_flow(client: TestClient, admin_headers: dict):
    # 1. Admin is already provisioned (seeded) and logged in.
    admin = admin_headers

    # 2. Admin creates a (non-admin) user.
    created = client.post(
        f"{API}/users",
        headers=admin,
        json={
            "name": "Team User",
            "email": "user@inwork.com",
            "password": "userPass1",
            "role": "user",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    # 3. Admin onboards a client.
    onboarded = client.post(f"{API}/clients/onboarding", headers=admin, json=onboarding_payload())
    assert onboarded.status_code == 201
    client_id = onboarded.json()["client"]["id"]

    # 4. User logs in.
    login = client.post(
        f"{API}/auth/login", json={"email": "user@inwork.com", "password": "userPass1"}
    )
    assert login.status_code == 200
    user = _auth(login.json()["access_token"])

    # 5. Before assignment the user is fully scoped out.
    assert client.get(f"{API}/clients", headers=user).json()["total"] == 0
    assert (
        client.get(f"{API}/clients/{client_id}", headers=user).status_code == 404
    )  # existence hidden
    assert (
        client.post(
            f"{API}/clients/onboarding", headers=user, json=onboarding_payload()
        ).status_code
        == 403
    )
    assert client.get(f"{API}/users", headers=user).status_code == 403
    assert (
        client.post(
            f"{API}/clients/{client_id}/assignments", headers=user, json={"user_id": user_id}
        ).status_code
        == 403
    )

    # 6. Admin assigns the client to the user.
    assigned = client.post(
        f"{API}/clients/{client_id}/assignments", headers=admin, json={"user_id": user_id}
    )
    assert assigned.status_code == 201

    # 7. The user now sees exactly that client and can open it.
    listing = client.get(f"{API}/clients", headers=user).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == client_id
    assert client.get(f"{API}/clients/{client_id}", headers=user).status_code == 200

    # 8. Admin unassigns -> the user loses access again.
    assert (
        client.delete(f"{API}/clients/{client_id}/assignments/{user_id}", headers=admin).status_code
        == 204
    )
    assert client.get(f"{API}/clients", headers=user).json()["total"] == 0
    assert client.get(f"{API}/clients/{client_id}", headers=user).status_code == 404

    # 9. Admin always sees every client regardless of assignment.
    assert client.get(f"{API}/clients", headers=admin).json()["total"] == 1
