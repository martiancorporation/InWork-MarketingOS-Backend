"""Tests for the audit before/after change capture (field-level accountability)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.request_context import get_audit_changes, set_audit_changes
from app.models.enums import ClientStatus
from app.schemas.client import ClientUpdate
from app.services.audit_service import AuditService, field_changes
from app.services.client_service import ClientService
from tests.conftest import API
from tests.helpers import onboarding_payload


def test_field_changes_helper():
    before = {"status": "active", "name": "A"}
    after = {"status": "inactive", "name": "A"}
    assert field_changes(before, after) == {"status": {"before": "active", "after": "inactive"}}
    assert field_changes(before, before) is None  # nothing changed → None


def test_record_persists_changes_and_read_api_returns_them(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = uuid.uuid4()
    AuditService(db_session).record(
        entity="clients",
        action="clients.update",
        client_id=None,
        entity_id=cid,
        changes={"status": {"before": "active", "after": "inactive"}},
    )
    resp = client.get(f"{API}/audit?action=clients.update", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["items"] if r["action"] == "clients.update")
    assert row["changes"]["status"] == {"before": "active", "after": "inactive"}


def test_update_client_captures_before_after(
    client: TestClient, admin_headers: dict, db_session: Session
):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload()
    )
    cid = uuid.UUID(resp.json()["client"]["id"])

    # Drive the service directly (the audit MIDDLEWARE is disabled in the suite,
    # but the service still attaches the diff to the request context).
    set_audit_changes(None)
    ClientService(db_session).update_client(
        cid, ClientUpdate(status=ClientStatus.inactive, name="Renamed Co.")
    )
    changes = get_audit_changes()
    assert changes is not None
    assert changes["status"] == {"before": "active", "after": "inactive"}
    assert changes["name"]["after"] == "Renamed Co."


def test_no_op_update_records_no_changes(
    client: TestClient, admin_headers: dict, db_session: Session
):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name="Steady")
    )
    cid = uuid.UUID(resp.json()["client"]["id"])
    set_audit_changes(None)
    ClientService(db_session).update_client(cid, ClientUpdate(name="Steady"))  # same value
    assert get_audit_changes() is None  # nothing changed → no diff attached
