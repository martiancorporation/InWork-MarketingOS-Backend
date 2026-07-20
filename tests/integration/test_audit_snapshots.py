"""Audit before/after snapshots for create (add) and delete (remove).

Create records ``None → value``, delete records ``value → None`` — so the audit
log shows *what was added/removed*, not just the action. The audit middleware is
disabled in the suite, so (like test_audit_changes) we drive the service directly
and read the diff off the request context.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.request_context import get_audit_changes, set_audit_changes
from app.models.enums import ComplianceKind
from app.models.user import User
from app.schemas.campaign import CampaignCreate
from app.services.audit_service import created_changes, deleted_changes
from app.services.campaign_service import CampaignService
from tests.conftest import API
from tests.helpers import onboarding_payload

# ---- pure helpers ----


def test_created_changes():
    assert created_changes({"name": "Acme", "status": "active"}) == {
        "name": {"before": None, "after": "Acme"},
        "status": {"before": None, "after": "active"},
    }


def test_created_changes_skips_none_and_empty():
    assert created_changes({"a": None}) is None
    assert created_changes({"a": None, "b": "x"}) == {"b": {"before": None, "after": "x"}}


def test_deleted_changes_maps_enum_to_value():
    assert deleted_changes({"kind": ComplianceKind.banned}) == {
        "kind": {"before": "banned", "after": None}
    }


# ---- service wiring (campaign create + delete) ----


def _client_id(client, admin_headers):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload()
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["client"]["id"])


def test_campaign_create_and_delete_capture_snapshots(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    admin = db_session.scalar(select(User).where(User.email == "admin@test.com"))
    svc = CampaignService(db_session)

    set_audit_changes(None)
    camp = svc.create_campaign(
        cid, CampaignCreate(name="Q3 Push", status="active", budget_usd=5000), created_by=admin.id
    )
    added = get_audit_changes()
    assert added["name"] == {"before": None, "after": "Q3 Push"}
    assert added["status"] == {"before": None, "after": "active"}

    set_audit_changes(None)
    svc.delete_campaign(cid, camp.id)
    removed = get_audit_changes()
    assert removed["name"] == {"before": "Q3 Push", "after": None}
    assert removed["status"] == {"before": "active", "after": None}
