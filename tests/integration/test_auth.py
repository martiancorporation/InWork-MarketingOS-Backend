"""API tests: authentication (bootstrap signup + login)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API


def test_bootstrap_signup_creates_admin(client: TestClient):
    resp = client.post(
        f"{API}/auth/signup",
        json={"name": "Alex", "email": "alex@test.com", "password": "s3curePass"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "alex@test.com"
    assert body["user"]["role"] == "admin"  # first user bootstraps as admin


def test_signup_is_closed_after_first_user(client: TestClient):
    first = client.post(
        f"{API}/auth/signup",
        json={"name": "Admin", "email": "admin@test.com", "password": "adminPass1"},
    )
    assert first.status_code == 201
    second = client.post(
        f"{API}/auth/signup",
        json={"name": "Two", "email": "two@test.com", "password": "twoPass123"},
    )
    assert second.status_code == 403


def test_signup_rejects_weak_password(client: TestClient):
    # too short
    assert client.post(
        f"{API}/auth/signup", json={"name": "A", "email": "a@test.com", "password": "short1"}
    ).status_code == 422
    # letters only, no digit
    assert client.post(
        f"{API}/auth/signup", json={"name": "A", "email": "a@test.com", "password": "onlyletters"}
    ).status_code == 422


def test_signup_rejects_invalid_email(client: TestClient):
    assert client.post(
        f"{API}/auth/signup", json={"name": "A", "email": "not-an-email", "password": "abc12345"}
    ).status_code == 422


def test_login_success(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/auth/login", json={"email": "admin@test.com", "password": "adminPass1"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "admin"


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


def test_login_disabled_account_401(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user(email="dis@test.com")
    client.patch(f"{API}/users/{user['id']}", headers=admin_headers, json={"is_active": False})
    resp = client.post(f"{API}/auth/login", json={"email": "dis@test.com", "password": "userPass1"})
    assert resp.status_code == 401
