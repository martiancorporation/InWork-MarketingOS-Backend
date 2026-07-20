"""API tests: granular per-project RBAC (BE-03).

Assignments can scope which per-project capabilities a user holds on a client.
Admins and managers always pass; a plain user is limited to their assignment's
set; a legacy (NULL) set means "full". Inaccessible → 404, accessible-but-
unauthorized → 403.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.alert import Alert
from tests.conftest import API
from tests.helpers import onboarding_payload


def _cid(client: TestClient, admin_headers: dict, name: str = "Acme Co.") -> str:
    r = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert r.status_code == 201, r.text
    return r.json()["client"]["id"]


def _assign(client, admin_headers, cid, uid, caps=None):
    body: dict = {"user_id": uid}
    if caps is not None:
        body["capabilities"] = caps
    r = client.post(f"{API}/clients/{cid}/assignments", headers=admin_headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _connect(client, headers, cid, key="ga4"):
    return client.post(
        f"{API}/clients/{cid}/integrations/{key}/connect", headers=headers, json={}
    )


def _make_alert(db_session: Session, cid: str) -> str:
    alert = Alert(client_id=uuid.UUID(cid), title="CPL over target")
    db_session.add(alert)
    db_session.commit()
    return str(alert.id)


# ---- capabilities on the assignment row ---- #


def test_assignment_defaults_to_full_capability_set(client, admin_headers, make_user):
    user, _ = make_user()
    cid = _cid(client, admin_headers)
    body = _assign(client, admin_headers, cid, user["id"])  # no capabilities → full
    caps = set(body["capabilities"])
    assert {"manage_integrations", "review_results", "review_creatives"} <= caps


def test_assignment_records_scoped_capabilities(client, admin_headers, make_user):
    user, _ = make_user()
    cid = _cid(client, admin_headers)
    body = _assign(client, admin_headers, cid, user["id"], caps=["review_results"])
    assert body["capabilities"] == ["review_results"]


def test_assignment_invalid_capability_422(client, admin_headers, make_user):
    user, _ = make_user()
    cid = _cid(client, admin_headers)
    r = client.post(
        f"{API}/clients/{cid}/assignments",
        headers=admin_headers,
        json={"user_id": user["id"], "capabilities": ["not_a_capability"]},
    )
    assert r.status_code == 422


def test_patch_capabilities(client, admin_headers, make_user):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["review_results"])
    assert _connect(client, uh, cid).status_code == 403  # lacks manage_integrations

    patched = client.patch(
        f"{API}/clients/{cid}/assignments/{user['id']}",
        headers=admin_headers,
        json={"capabilities": ["manage_integrations"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["capabilities"] == ["manage_integrations"]
    assert _connect(client, uh, cid).status_code == 200  # now allowed


def test_patch_unknown_assignment_404(client, admin_headers, make_user):
    user, _ = make_user()
    cid = _cid(client, admin_headers)
    r = client.patch(
        f"{API}/clients/{cid}/assignments/{user['id']}",
        headers=admin_headers,
        json={"capabilities": ["review_results"]},
    )
    assert r.status_code == 404


# ---- enforcement: integrations connect = manage_integrations ---- #


def test_connect_allowed_with_capability(client, admin_headers, make_user):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["manage_integrations"])
    assert _connect(client, uh, cid).status_code == 200


def test_connect_forbidden_without_capability_403(client, admin_headers, make_user):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["review_results"])
    assert _connect(client, uh, cid).status_code == 403


def test_connect_unassigned_user_404_not_403(client, admin_headers, make_user):
    _user, uh = make_user()
    cid = _cid(client, admin_headers)  # user is NOT assigned
    assert _connect(client, uh, cid).status_code == 404


def test_legacy_full_set_can_connect(client, admin_headers, make_user):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"])  # NULL → full set
    assert _connect(client, uh, cid).status_code == 200


def test_manager_overrides_scoped_capabilities(client, admin_headers, make_user):
    mgr, mh = make_user(email="mgr@test.com", role="manager")
    cid = _cid(client, admin_headers)
    # Even a deliberately narrow grant — a manager still gets the full set.
    _assign(client, admin_headers, cid, mgr["id"], caps=["review_results"])
    assert _connect(client, mh, cid).status_code == 200


def test_admin_always_passes(client, admin_headers):
    cid = _cid(client, admin_headers)
    assert _connect(client, admin_headers, cid).status_code == 200


# ---- enforcement: alert ack + rec decision = review_results ---- #


def test_alert_ack_requires_review_results(client, admin_headers, make_user, db_session):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["manage_integrations"])
    alert_id = _make_alert(db_session, cid)
    r = client.post(f"{API}/clients/{cid}/alerts/{alert_id}/acknowledge", headers=uh)
    assert r.status_code == 403


def test_alert_ack_allowed_with_review_results(client, admin_headers, make_user, db_session):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["review_results"])
    alert_id = _make_alert(db_session, cid)
    r = client.post(f"{API}/clients/{cid}/alerts/{alert_id}/acknowledge", headers=uh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "acknowledged"


def test_recommendation_decision_requires_review_results(client, admin_headers, make_user):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["manage_integrations"])
    r = client.post(
        f"{API}/clients/{cid}/recommendations/rec-x/decision",
        headers=uh,
        json={"decision": "accepted"},
    )
    assert r.status_code == 403


def test_recommendation_decision_allowed_with_review_results(client, admin_headers, make_user):
    user, uh = make_user()
    cid = _cid(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["review_results"])
    r = client.post(
        f"{API}/clients/{cid}/recommendations/rec-x/decision",
        headers=uh,
        json={"decision": "accepted"},
    )
    assert r.status_code == 201, r.text
