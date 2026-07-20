"""API tests: per-client outstanding-setup indicator (BE-05)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.enums import IntegrationKey, IntegrationStatus
from app.models.integration import Integration
from tests.conftest import API
from tests.helpers import onboarding_payload


def _cid(client: TestClient, admin_headers: dict, name: str = "Acme Co.") -> str:
    r = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert r.status_code == 201, r.text
    return r.json()["client"]["id"]


def test_fresh_client_has_outstanding_items(client: TestClient, admin_headers: dict):
    cid = _cid(client, admin_headers)
    r = client.get(f"{API}/clients/{cid}/setup", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {item["key"] for item in body["items"]}
    # A freshly onboarded client has no integrations and no intelligence profile.
    assert "no_integrations" in keys
    assert "no_intelligence_profile" in keys
    assert body["complete"] is False
    assert body["count"] == len(body["items"]) >= 2


def test_fully_set_up_client_is_complete(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _cid(client, admin_headers)
    obj = db_session.get(Client, uuid.UUID(cid))
    obj.current_profile_version = 1  # has an intelligence profile
    db_session.add(
        Integration(
            client_id=obj.id,
            key=IntegrationKey.ga4,
            status=IntegrationStatus.connected,
        )
    )
    db_session.commit()

    r = client.get(f"{API}/clients/{cid}/setup", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["complete"] is True
    assert body["count"] == 0
    assert body["items"] == []


def test_setup_unknown_client_404(client: TestClient, admin_headers: dict):
    r = client.get(
        f"{API}/clients/00000000-0000-0000-0000-000000000000/setup",
        headers=admin_headers,
    )
    assert r.status_code == 404


def test_setup_inaccessible_client_404(client: TestClient, admin_headers: dict, make_user):
    _user, uh = make_user()
    cid = _cid(client, admin_headers)  # user is not assigned
    r = client.get(f"{API}/clients/{cid}/setup", headers=uh)
    assert r.status_code == 404


def test_setup_requires_auth_401(client: TestClient, admin_headers: dict):
    cid = _cid(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/setup").status_code == 401
