"""API tests: the compliance register (entries + AI-rebuild sync + RBAC)."""

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


def _add(client, headers, cid, kind="banned", text="Never say 'guaranteed'."):
    resp = client.post(
        f"{API}/clients/{cid}/compliance", headers=headers, json={"kind": kind, "text": text}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_entry(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    body = _add(client, admin_headers, cid, kind="banned")
    assert body["kind"] == "banned"
    assert body["text"] == "Never say 'guaranteed'."
    assert body["is_active"] is True
    assert body["author_id"]


def test_list_and_kind_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)  # onboarding leaves one "note" entry
    _add(client, admin_headers, cid, kind="banned")
    _add(client, admin_headers, cid, kind="required", text="Always include 'Made in USA'.")
    assert client.get(f"{API}/clients/{cid}/compliance?kind=banned", headers=admin_headers).json()["total"] == 1
    assert client.get(f"{API}/clients/{cid}/compliance?kind=required", headers=admin_headers).json()["total"] == 1
    # note (from onboarding) + banned + required
    assert client.get(f"{API}/clients/{cid}/compliance", headers=admin_headers).json()["total"] == 3


def test_deactivate_hidden_from_active_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    entry = _add(client, admin_headers, cid, kind="rule", text="No emojis in LSA ads.")
    patched = client.patch(
        f"{API}/clients/{cid}/compliance/{entry['id']}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert patched.status_code == 200 and patched.json()["is_active"] is False
    active = client.get(f"{API}/clients/{cid}/compliance?active_only=true", headers=admin_headers).json()
    assert all(e["id"] != entry["id"] for e in active["items"])


def test_edit_text(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    entry = _add(client, admin_headers, cid, kind="note", text="Old note")
    resp = client.patch(
        f"{API}/clients/{cid}/compliance/{entry['id']}",
        headers=admin_headers,
        json={"text": "Updated note"},
    )
    assert resp.status_code == 200 and resp.json()["text"] == "Updated note"


def test_delete_entry(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    entry = _add(client, admin_headers, cid, kind="banned")
    assert client.delete(f"{API}/clients/{cid}/compliance/{entry['id']}", headers=admin_headers).status_code == 200
    remaining = client.get(f"{API}/clients/{cid}/compliance?kind=banned", headers=admin_headers).json()
    assert remaining["total"] == 0


def test_sync_returns_intelligence_status(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(f"{API}/clients/{cid}/compliance/sync", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] in {"none", "building", "ready", "failed"}


def test_entries_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    entry = _add(client, admin_headers, cid_a, kind="banned")
    resp = client.patch(
        f"{API}/clients/{cid_b}/compliance/{entry['id']}",
        headers=admin_headers,
        json={"text": "x"},
    )
    assert resp.status_code == 404


def test_assigned_user_can_manage(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]})
    body = _add(client, user_headers, cid, kind="rule", text="Tone: warm, expert.")
    assert body["author_id"] == user["id"]


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/compliance", headers=user_headers).status_code == 404


def test_compliance_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/compliance").status_code == 401
