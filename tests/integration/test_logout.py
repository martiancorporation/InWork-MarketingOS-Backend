"""API tests: token revocation / logout (BE-16).

Login mints a revocable token backed by a server-side session; logout deletes
the session so the token stops working even before it expires.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API


def _login(client: TestClient, email: str, password: str) -> str:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_logout_revokes_current_token(client: TestClient, admin_headers: dict):
    # A normal authenticated request works before logout.
    assert client.get(f"{API}/clients", headers=admin_headers).status_code == 200

    r = client.post(f"{API}/auth/logout", headers=admin_headers)
    assert r.status_code == 204

    # The same token is now dead even though its signature is still valid.
    assert client.get(f"{API}/clients", headers=admin_headers).status_code == 401


def test_logout_requires_authentication(client: TestClient):
    assert client.post(f"{API}/auth/logout").status_code == 401


def test_logout_only_revokes_that_session(client: TestClient, admin_headers: dict):
    # A second, independent login for the same user (admin_headers created one).
    token2 = _login(client, "admin@test.com", "adminPass1")
    headers2 = {"Authorization": f"Bearer {token2}"}

    # Revoke the first session.
    assert client.post(f"{API}/auth/logout", headers=admin_headers).status_code == 204

    # The second session is untouched.
    assert client.get(f"{API}/clients", headers=headers2).status_code == 200


def test_revoked_token_cannot_logout_again(client: TestClient, admin_headers: dict):
    assert client.post(f"{API}/auth/logout", headers=admin_headers).status_code == 204
    # Already revoked → auth fails before the endpoint runs.
    assert client.post(f"{API}/auth/logout", headers=admin_headers).status_code == 401
