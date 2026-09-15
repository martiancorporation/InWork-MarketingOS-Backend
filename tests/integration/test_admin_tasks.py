"""API tests: cross-client task view (Admin Panel "all tasks" list)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _create_task(client, headers, cid, **overrides):
    payload = {"title": "Task", "category": "strategy", "status": "todo"}
    payload.update(overrides)
    resp = client.post(f"{API}/clients/{cid}/plan/tasks", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_admin_sees_tasks_across_all_clients(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    _create_task(client, admin_headers, cid_a, title="A task")
    _create_task(client, admin_headers, cid_b, title="B task")

    resp = client.get(f"{API}/admin/tasks", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    titles = {item["title"]: item["client_name"] for item in body["items"]}
    assert titles.get("A task") == "Client A"
    assert titles.get("B task") == "Client B"


def test_non_admin_scoped_to_assigned_clients(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid_assigned = _client_id(client, admin_headers, name="Assigned Co.")
    cid_other = _client_id(client, admin_headers, name="Other Co.")
    client.post(
        f"{API}/clients/{cid_assigned}/assignments",
        headers=admin_headers,
        json={"user_id": user["id"]},
    )
    _create_task(client, admin_headers, cid_assigned, title="Visible task")
    _create_task(client, admin_headers, cid_other, title="Hidden task")

    resp = client.get(f"{API}/admin/tasks", headers=user_headers)
    assert resp.status_code == 200, resp.text
    titles = {item["title"] for item in resp.json()["items"]}
    assert "Visible task" in titles
    assert "Hidden task" not in titles


def test_filter_by_client_status_priority(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, title="High prio", priority="high")
    _create_task(client, admin_headers, cid, title="Low prio", priority="low")

    resp = client.get(
        f"{API}/admin/tasks",
        headers=admin_headers,
        params={"client_id": cid, "priority": "high"},
    )
    assert resp.status_code == 200, resp.text
    titles = {item["title"] for item in resp.json()["items"]}
    assert titles == {"High prio"}


def test_overdue_only_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    past = (date.today() - timedelta(days=3)).isoformat()
    future = (date.today() + timedelta(days=3)).isoformat()
    _create_task(client, admin_headers, cid, title="Overdue task", due_date=past)
    _create_task(client, admin_headers, cid, title="Upcoming task", due_date=future)

    resp = client.get(
        f"{API}/admin/tasks", headers=admin_headers, params={"client_id": cid, "overdue_only": True}
    )
    assert resp.status_code == 200, resp.text
    titles = {item["title"] for item in resp.json()["items"]}
    assert titles == {"Overdue task"}


def test_requires_auth(client: TestClient):
    assert client.get(f"{API}/admin/tasks").status_code == 401


def test_due_within_days_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    soon = (date.today() + timedelta(days=2)).isoformat()
    far = (date.today() + timedelta(days=30)).isoformat()
    _create_task(client, admin_headers, cid, title="Due soon", due_date=soon)
    _create_task(client, admin_headers, cid, title="Due later", due_date=far)

    resp = client.get(
        f"{API}/admin/tasks",
        headers=admin_headers,
        params={"client_id": cid, "due_within_days": 7},
    )
    assert resp.status_code == 200, resp.text
    titles = {item["title"] for item in resp.json()["items"]}
    assert titles == {"Due soon"}


def test_archived_tasks_excluded_by_default(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid, title="Will be archived")
    client.patch(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}",
        headers=admin_headers,
        json={"archived": True},
    )

    default = client.get(
        f"{API}/admin/tasks", headers=admin_headers, params={"client_id": cid}
    ).json()
    assert not any(t["title"] == "Will be archived" for t in default["items"])

    with_archived = client.get(
        f"{API}/admin/tasks",
        headers=admin_headers,
        params={"client_id": cid, "include_archived": True},
    ).json()
    assert any(t["title"] == "Will be archived" for t in with_archived["items"])


def test_workload_counts_open_and_overdue_per_assignee(
    client: TestClient, admin_headers: dict, make_user
):
    user, _ = make_user(email="workload@test.com")
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    past = (date.today() - timedelta(days=1)).isoformat()
    future = (date.today() + timedelta(days=5)).isoformat()
    _create_task(client, admin_headers, cid, title="Open 1", assignee_id=user["id"])
    _create_task(
        client, admin_headers, cid, title="Overdue 1", assignee_id=user["id"], due_date=past
    )
    _create_task(
        client, admin_headers, cid, title="Done task", assignee_id=user["id"], status="done"
    )
    _create_task(client, admin_headers, cid, title="Unassigned", due_date=future)

    resp = client.get(f"{API}/admin/tasks/workload", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["items"] if r["user_id"] == user["id"])
    assert row["user_name"]
    assert row["open_tasks"] == 2  # done + unassigned excluded
    assert row["overdue_tasks"] == 1


def test_workload_requires_auth(client: TestClient):
    assert client.get(f"{API}/admin/tasks/workload").status_code == 401
