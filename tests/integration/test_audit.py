"""API tests for the audit-log read endpoint + AuditService.record.

The middleware is disabled in the test env (see conftest), so these seed audit
rows directly through the service against the per-test session and assert the
read API's access control, filters, and pagination.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.client import Client
from app.services.audit_service import AuditService
from tests.conftest import API


def _seed(db: Session):
    # client_id is FK-bound, so create a real client row first.
    c = Client(slug=f"seed-{uuid.uuid4().hex[:8]}", name="Seed Co")
    db.add(c)
    db.commit()
    db.refresh(c)
    cid = c.id

    svc = AuditService(db)
    svc.record(entity="clients", action="clients.onboarding.create", client_id=cid,
               entity_id=cid, target_label="POST /clients/onboarding",
               meta={"status_code": 201, "method": "POST"})
    svc.record(entity="users", action="users.create", entity_id=uuid.uuid4(),
               target_label="POST /users", meta={"status_code": 201})
    svc.record(entity="auth", action="auth.login.create", meta={"status_code": 200})
    return cid


def test_record_persists_row(db_session: Session):
    row = AuditService(db_session).record(entity="clients", action="clients.read",
                                          meta={"status_code": 200})
    assert row.id is not None
    assert row.action == "clients.read"
    assert row.created_at is not None


def test_admin_lists_audit(client: TestClient, admin_headers: dict, db_session: Session):
    _seed(db_session)
    resp = client.get(f"{API}/audit", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert {"items", "total", "page", "page_size"} <= set(body)
    # newest first
    actions = [i["action"] for i in body["items"]]
    assert "auth.login.create" in actions and "users.create" in actions


def test_audit_filter_by_entity(client: TestClient, admin_headers: dict, db_session: Session):
    _seed(db_session)
    resp = client.get(f"{API}/audit?entity=clients", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items and all(i["entity"] == "clients" for i in items)


def test_audit_filter_by_action_substring(client: TestClient, admin_headers: dict, db_session: Session):
    _seed(db_session)
    resp = client.get(f"{API}/audit?action=login", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["action"] == "auth.login.create"


def test_audit_filter_by_client_id(client: TestClient, admin_headers: dict, db_session: Session):
    cid = _seed(db_session)
    resp = client.get(f"{API}/audit?client_id={cid}", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["client_id"] == str(cid)


def test_audit_pagination(client: TestClient, admin_headers: dict, db_session: Session):
    _seed(db_session)
    resp = client.get(f"{API}/audit?page=1&page_size=2", headers=admin_headers)
    body = resp.json()
    assert body["total"] == 3 and len(body["items"]) == 2 and body["page_size"] == 2


def test_audit_requires_admin(client: TestClient, make_user, db_session: Session):
    _seed(db_session)
    _, user_headers = make_user()
    assert client.get(f"{API}/audit", headers=user_headers).status_code == 403


def test_audit_requires_auth(client: TestClient):
    assert client.get(f"{API}/audit").status_code == 401
