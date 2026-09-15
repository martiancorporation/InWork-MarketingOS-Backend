"""API tests: client budgets (combined + per-platform, RBAC, validation)."""

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


def _assign(client, admin_headers, cid, uid, caps=None):
    body: dict = {"user_id": uid}
    if caps is not None:
        body["capabilities"] = caps
    resp = client.post(f"{API}/clients/{cid}/assignments", headers=admin_headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _put(client, headers, cid, **overrides):
    body = {"period": "2026-09", "amount_usd": 500}
    body.update(overrides)
    return client.put(f"{API}/clients/{cid}/budgets", headers=headers, json=body)


def test_set_combined_budget_defaults_platform(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _put(client, admin_headers, cid, amount_usd=1500)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["platform"] == "combined"
    assert float(body["amount_usd"]) == 1500
    assert body["set_by"]


def test_set_per_platform_budget(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _put(client, admin_headers, cid, platform="meta", amount_usd=800)
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform"] == "meta"


def test_upsert_overwrites_same_period_platform(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _put(client, admin_headers, cid, amount_usd=500)
    resp = _put(client, admin_headers, cid, amount_usd=750)
    assert resp.status_code == 200, resp.text
    assert float(resp.json()["amount_usd"]) == 750

    listed = client.get(
        f"{API}/clients/{cid}/budgets", headers=admin_headers, params={"period": "2026-09"}
    ).json()
    assert listed["combined"] is not None
    assert float(listed["combined"]["amount_usd"]) == 750


def test_get_period_returns_combined_and_per_platform(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _put(client, admin_headers, cid, amount_usd=2000)
    _put(client, admin_headers, cid, platform="meta", amount_usd=800)
    _put(client, admin_headers, cid, platform="google_ads", amount_usd=1200)

    resp = client.get(
        f"{API}/clients/{cid}/budgets", headers=admin_headers, params={"period": "2026-09"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "2026-09"
    assert float(body["combined"]["amount_usd"]) == 2000
    platforms = {row["platform"]: float(row["amount_usd"]) for row in body["by_platform"]}
    assert platforms == {"meta": 800, "google_ads": 1200}


def test_get_period_with_no_budgets_set(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.get(
        f"{API}/clients/{cid}/budgets", headers=admin_headers, params={"period": "2026-09"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["combined"] is None
    assert body["by_platform"] == []


def test_periods_are_independent(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _put(client, admin_headers, cid, period="2026-08", amount_usd=100)
    _put(client, admin_headers, cid, period="2026-09", amount_usd=200)
    aug = client.get(
        f"{API}/clients/{cid}/budgets", headers=admin_headers, params={"period": "2026-08"}
    ).json()
    sep = client.get(
        f"{API}/clients/{cid}/budgets", headers=admin_headers, params={"period": "2026-09"}
    ).json()
    assert float(aug["combined"]["amount_usd"]) == 100
    assert float(sep["combined"]["amount_usd"]) == 200


def test_invalid_period_format_rejected_on_put(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _put(client, admin_headers, cid, period="2026-9")
    assert resp.status_code == 422, resp.text


def test_invalid_period_format_rejected_on_get(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.get(
        f"{API}/clients/{cid}/budgets", headers=admin_headers, params={"period": "2026/09"}
    )
    assert resp.status_code == 400, resp.text


def test_negative_amount_rejected(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _put(client, admin_headers, cid, amount_usd=-1)
    assert resp.status_code == 422, resp.text


def test_unknown_field_rejected(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.put(
        f"{API}/clients/{cid}/budgets",
        headers=admin_headers,
        json={"period": "2026-09", "amount_usd": 500, "currency": "USD"},
    )
    assert resp.status_code == 422, resp.text


def test_budgets_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    _put(client, admin_headers, cid_a, amount_usd=999)
    resp = client.get(
        f"{API}/clients/{cid_b}/budgets", headers=admin_headers, params={"period": "2026-09"}
    )
    assert resp.status_code == 200
    assert resp.json()["combined"] is None


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    resp = client.get(
        f"{API}/clients/{cid}/budgets", headers=user_headers, params={"period": "2026-09"}
    )
    assert resp.status_code == 404


def test_assigned_user_without_manage_campaigns_cannot_set_budget(
    client: TestClient, admin_headers: dict, make_user
):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["review_results"])
    resp = _put(client, user_headers, cid, amount_usd=500)
    assert resp.status_code == 403, resp.text


def test_assigned_user_can_view_budget(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["review_results"])
    _put(client, admin_headers, cid, amount_usd=500)
    resp = client.get(
        f"{API}/clients/{cid}/budgets", headers=user_headers, params={"period": "2026-09"}
    )
    assert resp.status_code == 200
    assert float(resp.json()["combined"]["amount_usd"]) == 500


def test_assigned_user_with_manage_campaigns_can_set_budget(
    client: TestClient, admin_headers: dict, make_user
):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    _assign(client, admin_headers, cid, user["id"], caps=["manage_campaigns"])
    resp = _put(client, user_headers, cid, amount_usd=333)
    assert resp.status_code == 200, resp.text


def test_budgets_require_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.get(f"{API}/clients/{cid}/budgets", params={"period": "2026-09"})
    assert resp.status_code == 401
