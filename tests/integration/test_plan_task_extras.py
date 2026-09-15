"""API tests: PlanTask requirements/archived fields, supporting links,
comments, and duplication — the requirements-audit gap closures for the Task
Planning & Calendar System (attachments/links, a real "requirements" field,
and the "duplicate task"/"add notes" context-menu actions)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co.") -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _create_task(client, headers, cid, **overrides) -> dict:
    payload = {"title": "Draft the fall campaign brief", "category": "strategy"}
    payload.update(overrides)
    resp = client.post(f"{API}/clients/{cid}/plan/tasks", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_requirements_field_round_trips(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _create_task(
        client, admin_headers, cid, requirements="Must include the Q4 promo calendar."
    )
    assert task["requirements"] == "Must include the Q4 promo calendar."

    resp = client.patch(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}",
        headers=admin_headers,
        json={"requirements": "Updated: also needs legal sign-off."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["requirements"] == "Updated: also needs legal sign-off."


def test_task_detail_includes_empty_assets_and_notes(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid)
    resp = client.get(f"{API}/clients/{cid}/plan/tasks/{task['id']}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assets"] == []
    assert body["notes"] == []


def test_add_and_remove_supporting_link(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid)

    resp = client.post(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}/assets",
        headers=admin_headers,
        json={"url": "https://drive.example.com/brief", "label": "Creative brief"},
    )
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["url"] == "https://drive.example.com/brief"
    assert asset["label"] == "Creative brief"

    detail = client.get(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}", headers=admin_headers
    ).json()
    assert len(detail["assets"]) == 1

    remove = client.delete(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}/assets/{asset['id']}", headers=admin_headers
    )
    assert remove.status_code == 200, remove.text
    detail_after = client.get(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}", headers=admin_headers
    ).json()
    assert detail_after["assets"] == []


def test_supporting_link_cap_enforced(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid)
    for i in range(20):
        resp = client.post(
            f"{API}/clients/{cid}/plan/tasks/{task['id']}/assets",
            headers=admin_headers,
            json={"url": f"https://example.com/{i}"},
        )
        assert resp.status_code == 201, resp.text

    over_cap = client.post(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}/assets",
        headers=admin_headers,
        json={"url": "https://example.com/one-too-many"},
    )
    assert over_cap.status_code == 400


def test_add_note(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid)

    resp = client.post(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}/notes",
        headers=admin_headers,
        json={"body": "Waiting on client approval before we proceed."},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"] == "Waiting on client approval before we proceed."

    detail = client.get(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}", headers=admin_headers
    ).json()
    assert len(detail["notes"]) == 1
    assert detail["notes"][0]["body"] == "Waiting on client approval before we proceed."


def test_duplicate_task_resets_status_and_assignee(
    client: TestClient, admin_headers: dict, make_user
):
    user, _ = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    task = _create_task(
        client,
        admin_headers,
        cid,
        assignee_id=user["id"],
        status="in_progress",
        requirements="Needs the new logo files.",
    )

    resp = client.post(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}/duplicate", headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert copy["id"] != task["id"]
    assert copy["title"] == f"{task['title']} (copy)"
    assert copy["requirements"] == "Needs the new logo files."
    assert copy["status"] == "todo"
    assert copy["assignee_id"] is None


def test_archive_and_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid)

    resp = client.patch(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}",
        headers=admin_headers,
        json={"archived": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived"] is True

    default_list = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers).json()
    assert not any(t["id"] == task["id"] for t in default_list["items"])

    with_archived = client.get(
        f"{API}/clients/{cid}/plan/tasks?include_archived=true", headers=admin_headers
    ).json()
    assert any(t["id"] == task["id"] for t in with_archived["items"])


def test_task_extras_require_client_access(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid)

    resp = client.post(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}/notes",
        headers=user_headers,
        json={"body": "hi"},
    )
    assert resp.status_code == 404
