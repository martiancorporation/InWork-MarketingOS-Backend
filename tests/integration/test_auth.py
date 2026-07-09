"""API tests: authentication (login only — there is no public sign-up)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API


def test_no_public_signup_endpoint(client: TestClient):
    # Sign-up was removed; the route must not exist.
    resp = client.post(
        f"{API}/auth/signup",
        json={"name": "X", "email": "x@test.com", "password": "abc12345"},
    )
    assert resp.status_code == 404


def test_login_success(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/auth/login", json={"email": "admin@test.com", "password": "adminPass1"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["role"] == "admin"


def test_login_wrong_password_401(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/auth/login", json={"email": "admin@test.com", "password": "nope12345"}
    )
    assert resp.status_code == 401


def test_login_unknown_email_401(client: TestClient):
    resp = client.post(
        f"{API}/auth/login", json={"email": "ghost@test.com", "password": "whatever1"}
    )
    assert resp.status_code == 401


def test_login_invalid_email_422(client: TestClient):
    resp = client.post(f"{API}/auth/login", json={"email": "not-an-email", "password": "x"})
    assert resp.status_code == 422


def test_login_disabled_account_401(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user(email="dis@test.com")
    client.patch(f"{API}/users/{user['id']}", headers=admin_headers, json={"is_active": False})
    resp = client.post(f"{API}/auth/login", json={"email": "dis@test.com", "password": "userPass1"})
    assert resp.status_code == 401
