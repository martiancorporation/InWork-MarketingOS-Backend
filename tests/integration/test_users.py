"""API tests: user management (admin only)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API


def test_admin_creates_user(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/users",
        headers=admin_headers,
        json={
            "name": "Manager",
            "email": "mgr@test.com",
            "password": "mgrPass123",
            "role": "manager",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "manager"
    assert resp.json()["is_active"] is True


def test_create_user_defaults_to_role_user(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/users",
        headers=admin_headers,
        json={"name": "Plain", "email": "plain@test.com", "password": "plainPass1"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "user"


def test_create_duplicate_email_409(client: TestClient, admin_headers: dict, make_user):
    make_user(email="dup@test.com")
    resp = client.post(
        f"{API}/users",
        headers=admin_headers,
        json={"name": "Dup", "email": "dup@test.com", "password": "dupPass123"},
    )
    assert resp.status_code == 409


def test_create_user_weak_password_422(client: TestClient, admin_headers: dict):
    # too short
    assert (
        client.post(
            f"{API}/users",
            headers=admin_headers,
            json={"name": "A", "email": "a@test.com", "password": "short1"},
        ).status_code
        == 422
    )
    # letters only, no digit
    assert (
        client.post(
            f"{API}/users",
            headers=admin_headers,
            json={"name": "A", "email": "b@test.com", "password": "onlyletters"},
        ).status_code
        == 422
    )


def test_create_user_invalid_email_422(client: TestClient, admin_headers: dict):
    assert (
        client.post(
            f"{API}/users",
            headers=admin_headers,
            json={"name": "A", "email": "not-an-email", "password": "goodPass1"},
        ).status_code
        == 422
    )


def test_create_user_invalid_role_422(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/users",
        headers=admin_headers,
        json={
            "name": "Bad",
            "email": "bad@test.com",
            "password": "badPass123",
            "role": "superuser",
        },
    )
    assert resp.status_code == 422


def test_admin_lists_users(client: TestClient, admin_headers: dict, make_user):
    make_user(email="u1@test.com")
    make_user(email="u2@test.com")
    resp = client.get(f"{API}/users", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 3  # admin + 2


def test_admin_updates_user_role_and_status(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user(email="up@test.com")
    resp = client.patch(
        f"{API}/users/{user['id']}",
        headers=admin_headers,
        json={"role": "manager", "is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "manager"
    assert resp.json()["is_active"] is False


def test_update_unknown_user_404(client: TestClient, admin_headers: dict):
    resp = client.patch(
        f"{API}/users/00000000-0000-0000-0000-000000000000",
        headers=admin_headers,
        json={"role": "manager"},
    )
    assert resp.status_code == 404


def test_non_admin_cannot_manage_users(client: TestClient, make_user):
    _, user_headers = make_user()
    assert client.get(f"{API}/users", headers=user_headers).status_code == 403
    assert (
        client.post(
            f"{API}/users",
            headers=user_headers,
            json={"name": "X", "email": "x@test.com", "password": "xPass1234"},
        ).status_code
        == 403
    )


def test_users_require_authentication(client: TestClient):
    assert client.get(f"{API}/users").status_code == 401
