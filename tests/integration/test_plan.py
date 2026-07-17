"""API tests: plan / task board (kanban CRUD + filters + RBAC)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _task_payload(**overrides):
    payload = {
        "title": "Draft Q3 strategy deck",
        "description": "Outline the pillars for the quarter.",
        "category": "strategy",
        "status": "todo",
    }
    payload.update(overrides)
    return payload


def _create_task(client, headers, cid, **overrides):
    resp = client.post(
        f"{API}/clients/{cid}/plan/tasks", headers=headers, json=_task_payload(**overrides)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_task(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    body = _create_task(client, admin_headers, cid)
    assert body["title"] == "Draft Q3 strategy deck"
    assert body["category"] == "strategy"
    assert body["status"] == "todo"  # default
    assert body["assignee_id"] is None
    assert body["created_by"] is not None


def test_list_filter_by_status(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, status="todo", title="Not started")
    _create_task(client, admin_headers, cid, status="done", title="Finished task")
    resp = client.get(f"{API}/clients/{cid}/plan/tasks?status=done", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Finished task"


def test_get_task_detail(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_task(client, admin_headers, cid)
    resp = client.get(
        f"{API}/clients/{cid}/plan/tasks/{created['id']}", headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "Outline the pillars for the quarter."


def test_partial_update_preserves_other_fields(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_task(client, admin_headers, cid)
    # move the card todo -> in_progress
    resp = client.patch(
        f"{API}/clients/{cid}/plan/tasks/{created['id']}",
        headers=admin_headers,
        json={"status": "in_progress"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_progress"
    # untouched fields survive the partial patch
    assert body["title"] == "Draft Q3 strategy deck"
    assert body["category"] == "strategy"


def test_reassign_via_patch(client: TestClient, admin_headers: dict, make_user):
    user, _ = make_user()
    cid = _client_id(client, admin_headers)
    created = _create_task(client, admin_headers, cid)
    resp = client.patch(
        f"{API}/clients/{cid}/plan/tasks/{created['id']}",
        headers=admin_headers,
        json={"assignee_id": user["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["assignee_id"] == user["id"]


def test_delete_task(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_task(client, admin_headers, cid)
    resp = client.delete(
        f"{API}/clients/{cid}/plan/tasks/{created['id']}", headers=admin_headers
    )
    assert resp.status_code == 200
    gone = client.get(
        f"{API}/clients/{cid}/plan/tasks/{created['id']}", headers=admin_headers
    )
    assert gone.status_code == 404


def test_tasks_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    created = _create_task(client, admin_headers, cid_a)
    # a task of client A must not be reachable under client B's path
    resp = client.get(
        f"{API}/clients/{cid_b}/plan/tasks/{created['id']}", headers=admin_headers
    )
    assert resp.status_code == 404


def test_assigned_user_can_manage_plan(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    # assigned non-admin can create and list
    created = _create_task(client, user_headers, cid)
    assert created["created_by"] == user["id"]
    resp = client.get(f"{API}/clients/{cid}/plan/tasks", headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    # unassigned user can't even tell the client exists
    assert client.get(f"{API}/clients/{cid}/plan/tasks", headers=user_headers).status_code == 404
    assert (
        client.post(
            f"{API}/clients/{cid}/plan/tasks", headers=user_headers, json=_task_payload()
        ).status_code
        == 404
    )


def test_bad_status_enum_422(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        json=_task_payload(status="not_a_status"),
    )
    assert resp.status_code == 422


def test_plan_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/plan/tasks").status_code == 401
