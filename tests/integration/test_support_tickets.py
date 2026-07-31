"""Integration tests for the Support & Feedback Ticket System.

A global, owner-scoped resource (no ``client_id``) — a user only ever sees/
touches their own tickets, an admin sees and can act on every ticket. The S3
backend is replaced with an in-memory ``FakeStorage`` (same fixture shape as
``test_uploads.py``) so attachment tests don't touch AWS.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_storage
from app.main import app

API = "/api/v1"

multipart = pytest.importorskip("multipart", reason="python-multipart not installed")


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.is_configured = True

    def upload(self, fileobj, key, content_type):
        data = fileobj.read()
        self.objects[key] = {"size": len(data), "content_type": content_type}

    def generate_download_url(self, key, expiry_seconds):
        return f"https://s3.example.test/{key}?signed=1"

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def storage() -> Generator[FakeStorage, None, None]:
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_storage, None)


def _upload_file(client: TestClient, headers: dict) -> str:
    resp = client.post(
        f"{API}/uploads",
        headers=headers,
        files={"file": ("screenshot.png", b"fake-bytes", "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create(client: TestClient, headers: dict, **overrides) -> dict:
    payload = {
        "subject": "Login button is unresponsive",
        "category": "bug",
        "description": "Clicking login does nothing on Safari.",
        "priority": "high",
    }
    payload.update(overrides)
    resp = client.post(f"{API}/support-tickets", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- create ----


def test_create_ticket_defaults_to_open_with_a_generated_ticket_number(
    client: TestClient, admin_headers, storage
) -> None:
    body = _create(client, admin_headers)
    assert body["status"] == "open"
    assert body["ticket_number"].startswith("TCK-")
    assert body["attachments"] == []
    assert body["replies"] == []


def test_create_ticket_with_attachments(client: TestClient, admin_headers, storage) -> None:
    upload_id = _upload_file(client, admin_headers)
    body = _create(client, admin_headers, attachment_upload_ids=[upload_id])
    assert len(body["attachments"]) == 1
    attachment = body["attachments"][0]
    assert attachment["upload_id"] == upload_id
    assert attachment["filename"] == "screenshot.png"
    assert attachment["download_url"]


def test_create_ticket_rejects_someone_elses_upload(client: TestClient, make_user, storage) -> None:
    _, other_headers = make_user(email="other@test.com")
    upload_id = _upload_file(client, other_headers)
    _, requester_headers = make_user(email="requester@test.com")
    resp = client.post(
        f"{API}/support-tickets",
        headers=requester_headers,
        json={
            "subject": "x",
            "category": "bug",
            "description": "y",
            "attachment_upload_ids": [upload_id],
        },
    )
    assert resp.status_code == 404


def test_create_ticket_requires_auth(client: TestClient, storage) -> None:
    resp = client.post(f"{API}/support-tickets", json={})
    assert resp.status_code == 401


def test_create_ticket_rejects_unknown_fields(client: TestClient, admin_headers, storage) -> None:
    resp = client.post(
        f"{API}/support-tickets",
        headers=admin_headers,
        json={
            "subject": "x",
            "category": "bug",
            "description": "y",
            "not_a_real_field": True,
        },
    )
    assert resp.status_code == 422


def test_create_ticket_rejects_missing_required_fields(
    client: TestClient, admin_headers, storage
) -> None:
    resp = client.post(f"{API}/support-tickets", headers=admin_headers, json={"subject": "x"})
    assert resp.status_code == 422


def test_create_ticket_rejects_invalid_enum_value(
    client: TestClient, admin_headers, storage
) -> None:
    resp = client.post(
        f"{API}/support-tickets",
        headers=admin_headers,
        json={"subject": "x", "category": "not-a-category", "description": "y"},
    )
    assert resp.status_code == 422


# ---- list ----


def test_list_scopes_to_own_tickets_for_a_non_admin(
    client: TestClient, admin_headers, make_user, storage
) -> None:
    user_json, user_headers = make_user(email="reporter@test.com")
    _create(client, user_headers, subject="mine")
    _create(client, admin_headers, subject="admin's")

    resp = client.get(f"{API}/support-tickets", headers=user_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["subject"] == "mine"


def test_list_returns_every_ticket_for_an_admin_with_pagination_shape(
    client: TestClient, admin_headers, make_user, storage
) -> None:
    _, user_headers = make_user(email="reporter2@test.com")
    _create(client, user_headers, subject="from user")
    _create(client, admin_headers, subject="from admin")

    resp = client.get(f"{API}/support-tickets?page=1&page_size=1", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["page"] == 1
    assert body["page_size"] == 1


def test_list_filters_by_status_category_priority_and_search(
    client: TestClient, admin_headers, storage
) -> None:
    _create(
        client,
        admin_headers,
        subject="Billing double-charge",
        category="billing",
        priority="urgent",
    )
    _create(
        client, admin_headers, subject="Feature idea", category="feature_request", priority="low"
    )

    resp = client.get(f"{API}/support-tickets?category=billing", headers=admin_headers)
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["category"] == "billing"

    resp = client.get(f"{API}/support-tickets?priority=low", headers=admin_headers)
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["priority"] == "low"

    resp = client.get(f"{API}/support-tickets?status=open", headers=admin_headers)
    assert resp.json()["total"] == 2

    resp = client.get(f"{API}/support-tickets?search=double-charge", headers=admin_headers)
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["subject"] == "Billing double-charge"


# ---- get ----


def test_get_ticket_404s_for_another_users_ticket(
    client: TestClient, admin_headers, make_user, storage
) -> None:
    _, owner_headers = make_user(email="owner@test.com")
    _, intruder_headers = make_user(email="intruder@test.com")
    ticket = _create(client, owner_headers)

    resp = client.get(f"{API}/support-tickets/{ticket['id']}", headers=intruder_headers)
    assert resp.status_code == 404


def test_get_ticket_visible_to_owner_and_admin(
    client: TestClient, admin_headers, make_user, storage
) -> None:
    _, owner_headers = make_user(email="owner2@test.com")
    ticket = _create(client, owner_headers)

    assert (
        client.get(f"{API}/support-tickets/{ticket['id']}", headers=owner_headers).status_code
        == 200
    )
    assert (
        client.get(f"{API}/support-tickets/{ticket['id']}", headers=admin_headers).status_code
        == 200
    )


# ---- update ----


def test_owner_can_edit_ticket_content(
    client: TestClient, admin_headers, make_user, storage
) -> None:
    _, owner_headers = make_user(email="editor@test.com")
    ticket = _create(client, owner_headers)

    resp = client.put(
        f"{API}/support-tickets/{ticket['id']}",
        headers=owner_headers,
        json={"subject": "Updated subject", "priority": "urgent"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == "Updated subject"
    assert body["priority"] == "urgent"
    assert body["status"] == "open"


def test_owner_cannot_change_status(client: TestClient, admin_headers, make_user, storage) -> None:
    _, owner_headers = make_user(email="editor2@test.com")
    ticket = _create(client, owner_headers)

    resp = client.put(
        f"{API}/support-tickets/{ticket['id']}",
        headers=owner_headers,
        json={"status": "resolved"},
    )
    assert resp.status_code == 403


def test_admin_can_change_status_and_add_a_reply(
    client: TestClient, admin_headers, make_user, storage
) -> None:
    _, owner_headers = make_user(email="editor3@test.com")
    ticket = _create(client, owner_headers)

    resp = client.put(
        f"{API}/support-tickets/{ticket['id']}",
        headers=admin_headers,
        json={"status": "in_progress", "reply": "Looking into this now."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "in_progress"
    assert len(body["replies"]) == 1
    assert body["replies"][0]["author_role"] == "admin"
    assert body["replies"][0]["message"] == "Looking into this now."


def test_update_ticket_404s_for_another_users_ticket(
    client: TestClient, make_user, storage
) -> None:
    _, owner_headers = make_user(email="editor4@test.com")
    _, intruder_headers = make_user(email="intruder2@test.com")
    ticket = _create(client, owner_headers)

    resp = client.put(
        f"{API}/support-tickets/{ticket['id']}",
        headers=intruder_headers,
        json={"subject": "hijacked"},
    )
    assert resp.status_code == 404


def test_update_ticket_rejects_unknown_fields(client: TestClient, admin_headers, storage) -> None:
    ticket = _create(client, admin_headers)
    resp = client.put(
        f"{API}/support-tickets/{ticket['id']}",
        headers=admin_headers,
        json={"not_a_real_field": True},
    )
    assert resp.status_code == 422


# ---- delete ----


def test_owner_can_delete_their_own_open_ticket(client: TestClient, make_user, storage) -> None:
    _, owner_headers = make_user(email="deleter@test.com")
    ticket = _create(client, owner_headers)

    resp = client.delete(f"{API}/support-tickets/{ticket['id']}", headers=owner_headers)
    assert resp.status_code == 200, resp.text
    assert (
        client.get(f"{API}/support-tickets/{ticket['id']}", headers=owner_headers).status_code
        == 404
    )


def test_owner_cannot_delete_once_ticket_leaves_open(
    client: TestClient, admin_headers, make_user, storage
) -> None:
    _, owner_headers = make_user(email="deleter2@test.com")
    ticket = _create(client, owner_headers)
    client.put(
        f"{API}/support-tickets/{ticket['id']}",
        headers=admin_headers,
        json={"status": "in_progress"},
    )

    resp = client.delete(f"{API}/support-tickets/{ticket['id']}", headers=owner_headers)
    assert resp.status_code == 403


def test_admin_can_always_delete(client: TestClient, admin_headers, make_user, storage) -> None:
    _, owner_headers = make_user(email="deleter3@test.com")
    ticket = _create(client, owner_headers)
    client.put(
        f"{API}/support-tickets/{ticket['id']}", headers=admin_headers, json={"status": "resolved"}
    )

    resp = client.delete(f"{API}/support-tickets/{ticket['id']}", headers=admin_headers)
    assert resp.status_code == 200, resp.text


def test_delete_ticket_404s_for_another_users_ticket(
    client: TestClient, make_user, storage
) -> None:
    _, owner_headers = make_user(email="deleter4@test.com")
    _, intruder_headers = make_user(email="intruder3@test.com")
    ticket = _create(client, owner_headers)

    resp = client.delete(f"{API}/support-tickets/{ticket['id']}", headers=intruder_headers)
    assert resp.status_code == 404
