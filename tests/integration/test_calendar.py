"""API tests: marketing calendar (events + approval workflow + RBAC)."""

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


def _event_payload(**overrides):
    payload = {
        "title": "Winter skincare drop",
        "type": "campaign",
        "platform": "instagram",
        "event_date": "2026-07-15",
        "event_time": "09:00:00",
        "stage": "scheduled",
        "description": "Push across social.",
        "post": {"caption": "Cozy season ✨", "hashtags": "#newdrop #winter"},
        "ad": {"budget_usd": 250, "objective": "leads", "bid_strategy": "Lowest cost"},
    }
    payload.update(overrides)
    return payload


def _create_event(client, headers, cid, **overrides):
    resp = client.post(
        f"{API}/clients/{cid}/calendar/events", headers=headers, json=_event_payload(**overrides)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_event_with_post_and_ad(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    body = _create_event(client, admin_headers, cid)
    assert body["title"] == "Winter skincare drop"
    assert body["post"]["caption"] == "Cozy season ✨"
    assert body["ad"]["objective"] == "leads"
    assert body["approval_status"] == "pending"  # default
    # creation is recorded in the activity log
    assert any(a["action"] == "created" for a in body["activity"])


def test_list_events_by_month(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create_event(client, admin_headers, cid, event_date="2026-07-10")
    _create_event(client, admin_headers, cid, event_date="2026-08-10")
    resp = client.get(
        f"{API}/clients/{cid}/calendar/events?year=2026&month=7", headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["event_date"] == "2026-07-10"


def test_list_filter_by_stage(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create_event(client, admin_headers, cid, stage="draft", title="A draft idea")
    _create_event(client, admin_headers, cid, stage="scheduled", title="Scheduled post")
    resp = client.get(f"{API}/clients/{cid}/calendar/events?stage=draft", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["title"] == "A draft idea"


def test_get_event_detail(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_event(client, admin_headers, cid)
    resp = client.get(f"{API}/clients/{cid}/calendar/events/{created['id']}", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["ad"]["bid_strategy"] == "Lowest cost"


def test_partial_update_preserves_other_fields(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_event(client, admin_headers, cid)
    resp = client.patch(
        f"{API}/clients/{cid}/calendar/events/{created['id']}",
        headers=admin_headers,
        json={"title": "Renamed campaign"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Renamed campaign"
    # untouched fields survive the partial patch
    assert body["platform"] == "instagram"
    assert body["post"]["caption"] == "Cozy season ✨"


def test_reschedule_event(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_event(client, admin_headers, cid)
    resp = client.patch(
        f"{API}/clients/{cid}/calendar/events/{created['id']}",
        headers=admin_headers,
        json={"event_date": "2026-07-20", "event_time": "14:30:00", "stage": "scheduled"},
    )
    assert resp.status_code == 200
    assert resp.json()["event_date"] == "2026-07-20"
    assert resp.json()["event_time"] == "14:30:00"


def test_approval_workflow(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_event(client, admin_headers, cid)
    # "Submit for review again" → pending with a note, approved_by stays empty
    resp = client.post(
        f"{API}/clients/{cid}/calendar/events/{created['id']}/approval",
        headers=admin_headers,
        json={"status": "pending", "note": "Updated hero image."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "pending"
    assert body["approved_by"] is None
    assert body["approval_note"] == "Updated hero image."
    assert any(a["action"] == "status_change" for a in body["activity"])
    # approve → approved_by is stamped with the actor
    resp = client.post(
        f"{API}/clients/{cid}/calendar/events/{created['id']}/approval",
        headers=admin_headers,
        json={"status": "approved", "note": "Ship it."},
    )
    assert resp.status_code == 200
    assert resp.json()["approval_status"] == "approved"
    assert resp.json()["approved_by"] is not None


def test_delete_event(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    created = _create_event(client, admin_headers, cid)
    resp = client.delete(
        f"{API}/clients/{cid}/calendar/events/{created['id']}", headers=admin_headers
    )
    assert resp.status_code == 200
    gone = client.get(f"{API}/clients/{cid}/calendar/events/{created['id']}", headers=admin_headers)
    assert gone.status_code == 404


def test_get_unknown_event_404(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.get(
        f"{API}/clients/{cid}/calendar/events/00000000-0000-0000-0000-000000000000",
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_events_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    created = _create_event(client, admin_headers, cid_a)
    # the event of client A must not be reachable under client B's path
    resp = client.get(
        f"{API}/clients/{cid_b}/calendar/events/{created['id']}", headers=admin_headers
    )
    assert resp.status_code == 404


def test_assigned_user_can_manage_calendar(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    # assigned non-admin can create and list
    created = _create_event(client, user_headers, cid)
    assert created["created_by"] == user["id"]
    resp = client.get(f"{API}/clients/{cid}/calendar/events", headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    # unassigned user can't even tell the client exists
    assert (
        client.get(f"{API}/clients/{cid}/calendar/events", headers=user_headers).status_code == 404
    )
    assert (
        client.post(
            f"{API}/clients/{cid}/calendar/events", headers=user_headers, json=_event_payload()
        ).status_code
        == 404
    )


def test_calendar_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/calendar/events").status_code == 401
