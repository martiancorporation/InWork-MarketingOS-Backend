"""API tests: KPI alerts — watchdog evaluation, dedup, auto-resolve, workflow, RBAC."""

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


def _campaign(client, headers, cid, **overrides):
    payload = {"name": "Camp", "status": "active", "budget_usd": 100000}
    payload.update(overrides)
    resp = client.post(f"{API}/clients/{cid}/campaigns", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _patch(client, headers, cid, camp_id, **metrics):
    assert (
        client.patch(
            f"{API}/clients/{cid}/campaigns/{camp_id}", headers=headers, json=metrics
        ).status_code
        == 200
    )


def _evaluate(client, headers, cid):
    resp = client.post(f"{API}/clients/{cid}/alerts/evaluate", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_evaluate_no_campaigns(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    result = _evaluate(client, admin_headers, cid)
    assert result["opened"] == 0 and result["evaluated_campaigns"] == 0


def test_cpl_breach_opens_high_severity_alert(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _campaign(client, admin_headers, cid, target_cpl=25)
    _patch(client, admin_headers, cid, camp, leads=2, spend=100)  # cpl=$50 → 100% over
    result = _evaluate(client, admin_headers, cid)
    assert result["opened"] == 1
    alert = result["alerts"][0]
    assert alert["metric"] == "cpl"
    assert alert["severity"] == "high"
    assert alert["actual"] == 50.0 and alert["threshold"] == 25.0
    assert alert["campaign_id"] == camp


def test_reevaluate_updates_not_duplicates(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _campaign(client, admin_headers, cid, target_cpl=25)
    _patch(client, admin_headers, cid, camp, leads=2, spend=100)
    _evaluate(client, admin_headers, cid)
    second = _evaluate(client, admin_headers, cid)
    assert second["opened"] == 0 and second["updated"] >= 1
    listing = client.get(f"{API}/clients/{cid}/alerts?status=open", headers=admin_headers).json()
    assert sum(1 for a in listing["items"] if a["metric"] == "cpl") == 1


def test_auto_resolve_when_breach_clears(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _campaign(client, admin_headers, cid, target_cpl=25)
    _patch(client, admin_headers, cid, camp, leads=2, spend=100)  # breach
    _evaluate(client, admin_headers, cid)
    _patch(client, admin_headers, cid, camp, leads=100, spend=100)  # cpl=$1, healthy
    result = _evaluate(client, admin_headers, cid)
    assert result["auto_resolved"] == 1
    assert not any(a["metric"] == "cpl" for a in result["alerts"])


def test_opportunity_on_strong_roas(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _campaign(client, admin_headers, cid)
    _patch(client, admin_headers, cid, camp, spend=100, revenue=500)  # 5x ROAS
    result = _evaluate(client, admin_headers, cid)
    kinds = {a["kind"] for a in result["alerts"]}
    assert "opportunity" in kinds


def test_acknowledge_and_resolve(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _campaign(client, admin_headers, cid, target_cpl=25)
    _patch(client, admin_headers, cid, camp, leads=2, spend=100)
    alert_id = _evaluate(client, admin_headers, cid)["alerts"][0]["id"]

    ack = client.post(
        f"{API}/clients/{cid}/alerts/{alert_id}/acknowledge", headers=admin_headers
    ).json()
    assert ack["status"] == "acknowledged" and ack["acknowledged_by"] is not None

    res = client.post(
        f"{API}/clients/{cid}/alerts/{alert_id}/resolve", headers=admin_headers
    ).json()
    assert res["status"] == "resolved" and res["resolved_by"] is not None


def test_list_filter_by_severity(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    camp = _campaign(client, admin_headers, cid, target_cpl=25)
    _patch(client, admin_headers, cid, camp, leads=2, spend=100)  # high
    _evaluate(client, admin_headers, cid)
    resp = client.get(f"{API}/clients/{cid}/alerts?severity=high", headers=admin_headers)
    assert resp.status_code == 200
    assert all(a["severity"] == "high" for a in resp.json()["items"])


def test_scoping_unassigned_404(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    _user, headers = make_user(email="nope@test.com")
    assert client.post(f"{API}/clients/{cid}/alerts/evaluate", headers=headers).status_code == 404


def test_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/alerts").status_code == 401
