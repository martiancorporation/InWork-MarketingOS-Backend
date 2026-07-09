"""Shared pytest fixtures.

Tests run against a fresh in-memory SQLite database per test (the models are
dialect-portable), with the ``get_db`` dependency overridden so no real
Postgres is needed. Environment is pinned to a hermetic ``test`` config BEFORE
the app is imported, so `.env.*` files never leak into the suite.
"""

from __future__ import annotations

import os

# Pin config before any app import (env.py reads APP_ENV at import time).
os.environ["APP_ENV"] = "test"
# Force the AI provider "not configured" so the suite is hermetic (no network).
# Tests that exercise the AI-configured branch monkeypatch the client instead.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-must-be-at-least-32-bytes-long-00")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from collections.abc import Callable, Generator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401  register all tables on Base.metadata
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402

API = "/api/v1"


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):  # enforce FK constraints on SQLite
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def admin_headers(client: TestClient, db_session: Session) -> dict[str, str]:
    """Provision an admin (as the seed script does), then log in for a token."""
    db_session.add(
        User(
            email="admin@test.com",
            name="Admin",
            password_hash=hash_password("adminPass1"),
            role=UserRole.admin,
        )
    )
    db_session.commit()
    resp = client.post(
        f"{API}/auth/login", json={"email": "admin@test.com", "password": "adminPass1"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def make_user(client: TestClient, admin_headers: dict[str, str]) -> Callable[..., tuple[dict, dict]]:
    """Factory: admin creates a user, returns (user_json, that_user's_auth_header)."""

    def _make(
        email: str = "user@test.com",
        password: str = "userPass1",
        role: str = "user",
        name: str = "Normal User",
    ) -> tuple[dict, dict]:
        created = client.post(
            f"{API}/users",
            headers=admin_headers,
            json={"name": name, "email": email, "password": password, "role": role},
        )
        assert created.status_code == 201, created.text
        login = client.post(f"{API}/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        header = {"Authorization": f"Bearer {login.json()['access_token']}"}
        return created.json(), header

    return _make
