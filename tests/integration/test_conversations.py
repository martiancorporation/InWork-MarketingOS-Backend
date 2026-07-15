"""API tests: the shared-inbox conversations domain (threads, messages, RBAC)."""

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


def _new_conversation(client, headers, cid, **overrides):
    body = {
        "subject": "Q3 campaign review",
        "body": "Please review the deck.",
        "category": "Campaigns",
        "folder": "sent",
        "recipients": [{"email": "jane@client.com", "kind": "to"}],
    }
    body.update(overrides)
    resp = client.post(f"{API}/clients/{cid}/conversations", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_conversation(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    conv = _new_conversation(client, admin_headers, cid)
    assert conv["subject"] == "Q3 campaign review"
    assert conv["is_read"] is True  # we composed it
    assert len(conv["messages"]) == 1
    msg = conv["messages"][0]
    assert msg["folder"] == "sent"
    assert msg["recipients"][0]["email"] == "jane@client.com"


def test_list_and_folder_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _new_conversation(client, admin_headers, cid, subject="Sent one", folder="sent")
    _new_conversation(client, admin_headers, cid, subject="Draft one", folder="drafts")
    all_threads = client.get(f"{API}/clients/{cid}/conversations", headers=admin_headers).json()
    assert all_threads["total"] == 2
    sent = client.get(f"{API}/clients/{cid}/conversations?folder=sent", headers=admin_headers).json()
    assert sent["total"] == 1
    assert sent["items"][0]["subject"] == "Sent one"


def test_search_and_category_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _new_conversation(client, admin_headers, cid, subject="Budget shift", category="Billing")
    _new_conversation(client, admin_headers, cid, subject="Creative review", category="Campaigns")
    found = client.get(f"{API}/clients/{cid}/conversations?search=budget", headers=admin_headers).json()
    assert found["total"] == 1 and found["items"][0]["subject"] == "Budget shift"
    billing = client.get(
        f"{API}/clients/{cid}/conversations?category=Billing", headers=admin_headers
    ).json()
    assert billing["total"] == 1


def test_reply_updates_thread(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    conv = _new_conversation(client, admin_headers, cid)
    reply = client.post(
        f"{API}/clients/{cid}/conversations/{conv['id']}/messages",
        headers=admin_headers,
        json={"body": "Thanks, will do.", "recipients": [{"email": "jane@client.com"}]},
    )
    assert reply.status_code == 201, reply.text
    full = client.get(
        f"{API}/clients/{cid}/conversations/{conv['id']}", headers=admin_headers
    ).json()
    assert len(full["messages"]) == 2


def test_mark_read_unread(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    conv = _new_conversation(client, admin_headers, cid)
    resp = client.patch(
        f"{API}/clients/{cid}/conversations/{conv['id']}",
        headers=admin_headers,
        json={"is_read": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_read"] is False


def test_star_and_move_message(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    conv = _new_conversation(client, admin_headers, cid)
    mid = conv["messages"][0]["id"]
    # star it
    starred = client.patch(
        f"{API}/clients/{cid}/conversations/{conv['id']}/messages/{mid}",
        headers=admin_headers,
        json={"is_starred": True},
    )
    assert starred.status_code == 200 and starred.json()["is_starred"] is True
    # it now shows under the starred pseudo-folder
    st = client.get(f"{API}/clients/{cid}/conversations?starred=true", headers=admin_headers).json()
    assert st["total"] == 1
    # move to archive
    moved = client.patch(
        f"{API}/clients/{cid}/conversations/{conv['id']}/messages/{mid}",
        headers=admin_headers,
        json={"folder": "archive"},
    )
    assert moved.json()["folder"] == "archive"


def test_delete_conversation(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    conv = _new_conversation(client, admin_headers, cid)
    resp = client.delete(f"{API}/clients/{cid}/conversations/{conv['id']}", headers=admin_headers)
    assert resp.status_code == 200
    gone = client.get(f"{API}/clients/{cid}/conversations/{conv['id']}", headers=admin_headers)
    assert gone.status_code == 404


def test_conversations_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    conv = _new_conversation(client, admin_headers, cid_a)
    resp = client.get(
        f"{API}/clients/{cid_b}/conversations/{conv['id']}", headers=admin_headers
    )
    assert resp.status_code == 404


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/conversations", headers=user_headers).status_code == 404


def test_assigned_user_can_use_inbox(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    conv = _new_conversation(client, user_headers, cid)
    assert conv["messages"][0]["sender_user_id"] == user["id"]
    assert client.get(f"{API}/clients/{cid}/conversations", headers=user_headers).json()["total"] == 1


def test_conversations_require_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/conversations").status_code == 401
