"""API tests: the report registry (create/list/get/update/delete + RBAC)."""

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


def _report_payload(**overrides):
    payload = {
        "kind": "performance",
        "format": "pdf",
        "title": "September performance",
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
        "scope": "holistic",
        "channels": ["meta", "google-ads"],
        "sections": ["spend", "campaigns", "leads"],
        "save_to_outlook_draft": True,
    }
    payload.update(overrides)
    return payload


def _create(client, headers, cid, **overrides):
    resp = client.post(f"{API}/clients/{cid}/reports", headers=headers, json=_report_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_report(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    body = _create(client, admin_headers, cid)
    assert body["title"] == "September performance"
    assert body["kind"] == "performance"
    assert body["channels"] == ["meta", "google-ads"]
    assert body["sections"] == ["spend", "campaigns", "leads"]
    assert body["save_to_outlook_draft"] is True
    assert body["created_by"]


def test_bad_date_range_422(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/reports",
        headers=admin_headers,
        json=_report_payload(date_from="2026-09-30", date_to="2026-09-01"),
    )
    assert resp.status_code == 422


def test_list_and_kind_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create(client, admin_headers, cid, kind="performance", title="Perf")
    _create(client, admin_headers, cid, kind="compliance", title="Compliance")
    assert client.get(f"{API}/clients/{cid}/reports", headers=admin_headers).json()["total"] == 2
    only = client.get(f"{API}/clients/{cid}/reports?kind=compliance", headers=admin_headers).json()
    assert only["total"] == 1 and only["items"][0]["title"] == "Compliance"


def test_get_report(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    rid = _create(client, admin_headers, cid)["id"]
    resp = client.get(f"{API}/clients/{cid}/reports/{rid}", headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["id"] == rid


def test_update_attaches_file(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    rid = _create(client, admin_headers, cid)["id"]
    resp = client.patch(
        f"{API}/clients/{cid}/reports/{rid}",
        headers=admin_headers,
        json={"file_url": "s3://bucket/report.pdf", "save_to_outlook_draft": False},
    )
    assert resp.status_code == 200
    assert resp.json()["file_url"] == "s3://bucket/report.pdf"
    assert resp.json()["save_to_outlook_draft"] is False


def test_delete_report(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    rid = _create(client, admin_headers, cid)["id"]
    assert client.delete(f"{API}/clients/{cid}/reports/{rid}", headers=admin_headers).status_code == 200
    assert client.get(f"{API}/clients/{cid}/reports/{rid}", headers=admin_headers).status_code == 404


def test_reports_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    rid = _create(client, admin_headers, cid_a)["id"]
    assert client.get(f"{API}/clients/{cid_b}/reports/{rid}", headers=admin_headers).status_code == 404


def test_assigned_user_can_manage_reports(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]})
    body = _create(client, user_headers, cid)
    assert body["created_by"] == user["id"]


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/reports", headers=user_headers).status_code == 404


def test_reports_require_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/reports").status_code == 401
