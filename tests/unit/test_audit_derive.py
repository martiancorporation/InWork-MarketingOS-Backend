"""Unit tests for the audit action/entity derivation (pure, no DB)."""

from __future__ import annotations

import uuid

import pytest

from app.services.audit_service import derive_audit

CID = uuid.uuid4()
UID = uuid.uuid4()


@pytest.mark.parametrize(
    "method,path,entity,has_id,action",
    [
        ("POST", "/api/v1/auth/login", "auth", False, "auth.login.create"),
        ("GET", "/api/v1/clients", "clients", False, "clients.read"),
        ("GET", f"/api/v1/clients/{CID}", "clients", True, "clients.read"),
        ("POST", "/api/v1/clients/onboarding", "clients", False, "clients.onboarding.create"),
        ("PATCH", f"/api/v1/clients/{CID}/onboarding", "clients", True, "clients.onboarding.update"),
        ("POST", f"/api/v1/clients/{CID}/onboarding/complete", "clients", True, "clients.onboarding.complete.create"),
        ("POST", f"/api/v1/clients/{CID}/documents", "clients", True, "clients.documents.create"),
        ("DELETE", f"/api/v1/clients/{CID}/assignments/{UID}", "clients", True, "clients.assignments.delete"),
        ("POST", "/api/v1/users", "users", False, "users.create"),
        ("PATCH", f"/api/v1/users/{UID}", "users", True, "users.update"),
    ],
)
def test_derive_audit(method, path, entity, has_id, action):
    got_entity, got_id, got_action = derive_audit(method, path)
    assert got_entity == entity
    assert (got_id is not None) == has_id
    assert got_action == action


def test_first_uuid_becomes_entity_id():
    _, entity_id, _ = derive_audit("DELETE", f"/api/v1/clients/{CID}/assignments/{UID}")
    assert entity_id == CID  # the first uuid in the path, not the second
