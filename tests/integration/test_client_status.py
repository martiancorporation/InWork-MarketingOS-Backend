"""API tests: client status lifecycle, onboarding progress %, and admin PATCH."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload

DRAFT = {
    "name": "Draftly Inc.",
    "business_type": "DTC E-commerce",
    "industry": "Home & Garden",
    "website": "https://draftly.com",
    "markets": "US",
}


def _draft_id(client, headers):
    resp = client.post(f"{API}/clients/onboarding/draft", headers=headers, json=DRAFT)
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _atomic_id(client, headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def test_draft_is_status_draft(client: TestClient, admin_headers: dict):
    cid = _draft_id(client, admin_headers)
    body = client.get(f"{API}/clients/{cid}", headers=admin_headers).json()
    assert body["status"] == "draft"
    assert body["onboarding_step"] == 1
    assert body["onboarding_percent"] == 13
    assert body["onboarding_completed"] is False


def test_complete_flips_draft_to_active(client: TestClient, admin_headers: dict):
    cid = _draft_id(client, admin_headers)
    client.post(f"{API}/clients/{cid}/onboarding/complete", headers=admin_headers)
    body = client.get(f"{API}/clients/{cid}", headers=admin_headers).json()
    assert body["status"] == "active"
    assert body["onboarding_percent"] == 100
    assert body["onboarding_completed"] is True


def test_atomic_onboarding_is_active(client: TestClient, admin_headers: dict):
    cid = _atomic_id(client, admin_headers)
    body = client.get(f"{API}/clients/{cid}", headers=admin_headers).json()
    assert body["status"] == "active"
    assert body["onboarding_completed"] is True


def test_list_includes_progress_and_status(client: TestClient, admin_headers: dict):
    _draft_id(client, admin_headers)
    resp = client.get(f"{API}/clients?search=Draftly", headers=admin_headers)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["status"] == "draft"
    assert item["onboarding_step"] == 1
    assert item["onboarding_percent"] == 13
    assert item["onboarding_completed"] is False


def test_admin_patch_status_to_inactive(client: TestClient, admin_headers: dict):
    cid = _atomic_id(client, admin_headers)
    resp = client.patch(f"{API}/clients/{cid}", headers=admin_headers, json={"status": "inactive"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"
    # persisted
    assert client.get(f"{API}/clients/{cid}", headers=admin_headers).json()["status"] == "inactive"


def test_patch_updates_basic_fields(client: TestClient, admin_headers: dict):
    cid = _atomic_id(client, admin_headers)
    resp = client.patch(
        f"{API}/clients/{cid}",
        headers=admin_headers,
        json={"location": "Kolkata", "industry": "SaaS"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["location"] == "Kolkata"
    assert body["industry"] == "SaaS"
    # status untouched by a partial patch
    assert body["status"] == "active"


def test_patch_rejects_bad_status(client: TestClient, admin_headers: dict):
    cid = _atomic_id(client, admin_headers)
    resp = client.patch(f"{API}/clients/{cid}", headers=admin_headers, json={"status": "bogus"})
    assert resp.status_code == 422


def test_patch_unknown_client_404(client: TestClient, admin_headers: dict):
    resp = client.patch(
        f"{API}/clients/00000000-0000-0000-0000-000000000000",
        headers=admin_headers,
        json={"status": "inactive"},
    )
    assert resp.status_code == 404


def test_non_admin_cannot_patch(client: TestClient, admin_headers: dict, make_user):
    cid = _atomic_id(client, admin_headers)
    _user, user_headers = make_user()
    resp = client.patch(f"{API}/clients/{cid}", headers=user_headers, json={"status": "inactive"})
    assert resp.status_code == 403
