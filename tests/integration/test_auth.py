"""API tests: authentication (login only — there is no public sign-up)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import API


def test_no_public_signup_endpoint(client: TestClient):
    # Sign-up was removed; the route must not exist.
    resp = client.post(
        f"{API}/auth/signup",
        json={"name": "X", "email": "x@test.com", "password": "abc123456789"},
    )
    assert resp.status_code == 404


def test_login_success(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/auth/login", json={"email": "admin@test.com", "password": "adminPass1234"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["role"] == "admin"


def test_login_wrong_password_401(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/auth/login", json={"email": "admin@test.com", "password": "nope123456789"}
    )
    assert resp.status_code == 401


def test_login_unknown_email_401(client: TestClient):
    resp = client.post(
        f"{API}/auth/login", json={"email": "ghost@test.com", "password": "whatever12345"}
    )
    assert resp.status_code == 401


def test_login_invalid_email_422(client: TestClient):
    resp = client.post(f"{API}/auth/login", json={"email": "not-an-email", "password": "x"})
    assert resp.status_code == 422


def test_login_disabled_account_401(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user(email="dis@test.com")
    client.patch(f"{API}/users/{user['id']}", headers=admin_headers, json={"is_active": False})
    resp = client.post(
        f"{API}/auth/login", json={"email": "dis@test.com", "password": "userPass1234"}
    )
    assert resp.status_code == 401


# ---- per-account lockout (app/services/auth_service.py) ----
#
# Exercised at the service layer on purpose: the route also carries the per-IP
# limiter (10/60s), which is deliberately *tighter* than the per-account one
# (20/300s), so from a single IP the IP limiter always fires first. The account
# backstop exists for the attack the IP limiter can't see — the same account
# hammered from many different addresses.


@pytest.fixture
def rate_limited(monkeypatch):
    """Turn the limiter on for one test (the suite disables it globally)."""
    from app.core import rate_limit
    from app.core.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    rate_limit.reset()
    yield
    rate_limit.reset()
    get_settings.cache_clear()


def _login(db, email: str, password: str):
    from app.schemas.auth import LoginRequest
    from app.services.auth_service import AuthService

    return AuthService(db).login(LoginRequest(email=email, password=password))


def test_repeated_failures_lock_the_account(client: TestClient, db_session, rate_limited):
    from app.core.exceptions import AuthError, TooManyRequestsError
    from app.services.auth_service import _ACCOUNT_LOCKOUT_TIMES

    _seed_user(db_session, "locked@test.com")
    for _ in range(_ACCOUNT_LOCKOUT_TIMES):
        with pytest.raises(AuthError):
            _login(db_session, "locked@test.com", "wrong-password")

    # Budget spent — even the CORRECT password is refused now, and as a
    # rate-limit error rather than a credential error.
    with pytest.raises(TooManyRequestsError):
        _login(db_session, "locked@test.com", "correctPass1234")


def test_successful_login_refunds_the_account_budget(client: TestClient, db_session, rate_limited):
    """A legitimate user's own logins must never contribute to locking them
    out — otherwise anyone who knows an email could hold a real account at 429
    indefinitely just by spending its budget."""
    from app.services.auth_service import _ACCOUNT_LOCKOUT_TIMES

    _seed_user(db_session, "refund@test.com")
    for _ in range(_ACCOUNT_LOCKOUT_TIMES * 3):
        user, token = _login(db_session, "refund@test.com", "correctPass1234")
        assert token


def test_failures_below_the_threshold_do_not_lock(client: TestClient, db_session, rate_limited):
    from app.core.exceptions import AuthError
    from app.services.auth_service import _ACCOUNT_LOCKOUT_TIMES

    _seed_user(db_session, "partial@test.com")
    for _ in range(_ACCOUNT_LOCKOUT_TIMES - 1):
        with pytest.raises(AuthError):
            _login(db_session, "partial@test.com", "wrong-password")
    # Still within budget: the right password works and refunds it.
    _user, token = _login(db_session, "partial@test.com", "correctPass1234")
    assert token


def _seed_user(db, email: str):
    from app.core.security import hash_password
    from app.models.enums import UserRole
    from app.models.user import User

    user = User(
        email=email,
        name="Lockout Test",
        password_hash=hash_password("correctPass1234"),
        role=UserRole.user,
    )
    db.add(user)
    db.commit()
    return user
