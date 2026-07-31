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
    resp = client.get(f"{API}/clients/{cid}/plan/tasks/{created['id']}", headers=admin_headers)
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
    resp = client.delete(f"{API}/clients/{cid}/plan/tasks/{created['id']}", headers=admin_headers)
    assert resp.status_code == 200
    gone = client.get(f"{API}/clients/{cid}/plan/tasks/{created['id']}", headers=admin_headers)
    assert gone.status_code == 404


def test_tasks_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    created = _create_task(client, admin_headers, cid_a)
    # a task of client A must not be reachable under client B's path
    resp = client.get(f"{API}/clients/{cid_b}/plan/tasks/{created['id']}", headers=admin_headers)
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


# --------------------------------------------------------------------------- #
# Multi-day spans + clock times
#
# These fields used to be accepted by the API and silently discarded (the request
# schema was a plain ``BaseModel``, so Pydantic ignored unknown keys and still
# answered 201). The web client had already shipped spanning bars against them,
# so a campaign "1-31 July" looked correct until the page was reloaded.
# --------------------------------------------------------------------------- #


def test_multi_day_span_round_trips(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    body = _create_task(
        client,
        admin_headers,
        cid,
        title="July always-on campaign",
        start_date="2026-07-01",
        due_date="2026-07-31",
        start_time="09:30",
        end_time="17:00",
    )
    assert body["start_date"] == "2026-07-01"
    assert body["due_date"] == "2026-07-31"
    assert body["start_time"] == "09:30:00"
    assert body["end_time"] == "17:00:00"

    # The reload path is the one that used to lose the data.
    fetched = client.get(f"{API}/clients/{cid}/plan/tasks/{body['id']}", headers=admin_headers)
    assert fetched.status_code == 200
    assert fetched.json()["start_date"] == "2026-07-01"
    assert fetched.json()["due_date"] == "2026-07-31"
    assert fetched.json()["start_time"] == "09:30:00"


def test_single_day_and_undated_tasks_are_still_valid(client: TestClient, admin_headers: dict):
    """Only ``due_date``, only ``start_date``, or neither — all legitimate."""
    cid = _client_id(client, admin_headers)
    due_only = _create_task(client, admin_headers, cid, due_date="2026-07-10")
    assert due_only["due_date"] == "2026-07-10" and due_only["start_date"] is None
    start_only = _create_task(client, admin_headers, cid, start_date="2026-07-11")
    assert start_only["start_date"] == "2026-07-11" and start_only["due_date"] is None
    undated = _create_task(client, admin_headers, cid)
    assert undated["start_date"] is None and undated["due_date"] is None


def test_unknown_field_is_rejected_not_ignored(client: TestClient, admin_headers: dict):
    """``StrictModel`` turns a typo into a 422 instead of silent data loss."""
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        json=_task_payload(startdate="2026-07-01"),  # missing underscore
    )
    assert resp.status_code == 422, resp.text


def test_inverted_ranges_are_rejected(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    dates = client.post(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        json=_task_payload(start_date="2026-07-31", due_date="2026-07-01"),
    )
    assert dates.status_code == 422, dates.text
    times = client.post(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        json=_task_payload(start_time="18:00", end_time="09:00"),
    )
    assert times.status_code == 422, times.text


def test_patch_cannot_invert_a_span_one_edge_at_a_time(client: TestClient, admin_headers: dict):
    """The schema only sees what was sent, so the merged row is checked too."""
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid, start_date="2026-07-10", due_date="2026-07-20")
    resp = client.patch(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}",
        headers=admin_headers,
        json={"due_date": "2026-07-01"},  # before the stored start_date
    )
    assert resp.status_code == 400, resp.text
    # The stored row is unchanged.
    after = client.get(f"{API}/clients/{cid}/plan/tasks/{task['id']}", headers=admin_headers)
    assert after.json()["due_date"] == "2026-07-20"


def test_patch_can_move_a_whole_span(client: TestClient, admin_headers: dict):
    """Dragging a bar sends both edges at once."""
    cid = _client_id(client, admin_headers)
    task = _create_task(client, admin_headers, cid, start_date="2026-07-10", due_date="2026-07-20")
    resp = client.patch(
        f"{API}/clients/{cid}/plan/tasks/{task['id']}",
        headers=admin_headers,
        json={"start_date": "2026-08-10", "due_date": "2026-08-20"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["start_date"] == "2026-08-10"
    assert resp.json()["due_date"] == "2026-08-20"


# --------------------------------------------------------------------------- #
# Calendar date window
# --------------------------------------------------------------------------- #


def test_date_window_uses_overlap_not_containment(client: TestClient, admin_headers: dict):
    """A span straddling the window must appear; one outside must not.

    This is the whole point of an overlap predicate: viewing 10-16 July has to
    show a campaign that runs 1-31 July even though neither of its edges is in
    the window.
    """
    cid = _client_id(client, admin_headers)
    _create_task(
        client,
        admin_headers,
        cid,
        title="Straddles",
        start_date="2026-07-01",
        due_date="2026-07-31",
    )
    _create_task(client, admin_headers, cid, title="Inside", due_date="2026-07-14")
    _create_task(client, admin_headers, cid, title="Before", due_date="2026-06-01")
    _create_task(client, admin_headers, cid, title="After", due_date="2026-09-01")
    _create_task(client, admin_headers, cid, title="Undated")

    resp = client.get(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        params={"start": "2026-07-10", "end": "2026-07-16"},
    )
    assert resp.status_code == 200, resp.text
    titles = {t["title"] for t in resp.json()["items"]}
    assert titles == {"Straddles", "Inside"}
    assert resp.json()["total"] == 2  # the count must respect the window too


def test_date_window_boundaries_are_inclusive(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, title="On start", due_date="2026-07-10")
    _create_task(client, admin_headers, cid, title="On end", due_date="2026-07-16")
    resp = client.get(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        params={"start": "2026-07-10", "end": "2026-07-16"},
    )
    assert {t["title"] for t in resp.json()["items"]} == {"On start", "On end"}


def test_windowed_list_is_chronological(client: TestClient, admin_headers: dict):
    """Newest-first is right for a board, wrong for a calendar."""
    cid = _client_id(client, admin_headers)
    for title, day in (("third", "2026-07-20"), ("first", "2026-07-05"), ("second", "2026-07-12")):
        _create_task(client, admin_headers, cid, title=title, due_date=day)
    resp = client.get(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        params={"start": "2026-07-01", "end": "2026-07-31"},
    )
    assert [t["title"] for t in resp.json()["items"]] == ["first", "second", "third"]


def test_unwindowed_list_still_returns_undated_tasks(client: TestClient, admin_headers: dict):
    """The board shows everything; only a windowed (calendar) query filters."""
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, title="Undated")
    resp = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert {t["title"] for t in resp.json()["items"]} == {"Undated"}


def test_include_undated_adds_the_unscheduled_pile(client: TestClient, admin_headers: dict):
    """The board shows a window *plus* undated tasks; the calendar shows only the window."""
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, title="In window", due_date="2026-07-14")
    _create_task(client, admin_headers, cid, title="Undated")
    _create_task(client, admin_headers, cid, title="Other month", due_date="2026-09-01")

    params = {"start": "2026-07-01", "end": "2026-07-31"}
    calendar = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers, params=params)
    assert {t["title"] for t in calendar.json()["items"]} == {"In window"}

    board = client.get(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        params={**params, "include_undated": "true"},
    )
    assert {t["title"] for t in board.json()["items"]} == {"In window", "Undated"}
    assert board.json()["total"] == 2


def test_inverted_window_is_rejected(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.get(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        params={"start": "2026-07-31", "end": "2026-07-01"},
    )
    assert resp.status_code == 400, resp.text
