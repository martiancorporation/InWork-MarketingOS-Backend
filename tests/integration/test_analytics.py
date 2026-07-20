"""API tests: analytics ingest (upsert) + aggregation summaries + RBAC."""

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


def _ingest(client, headers, cid, rows):
    resp = client.post(
        f"{API}/clients/{cid}/analytics/ingest", headers=headers, json={"rows": rows}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


SEED = [
    {
        "date": "2026-09-01",
        "platform": "instagram",
        "impressions": 1000,
        "clicks": 50,
        "leads": 5,
        "spend": 100,
        "revenue": 300,
    },
    {
        "date": "2026-09-01",
        "platform": "facebook",
        "impressions": 2000,
        "clicks": 80,
        "leads": 10,
        "spend": 150,
        "revenue": 450,
    },
    {
        "date": "2026-09-02",
        "platform": "instagram",
        "impressions": 1200,
        "clicks": 60,
        "leads": 6,
        "spend": 120,
        "revenue": 360,
    },
]


def test_ingest_and_summary(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert _ingest(client, admin_headers, cid, SEED)["upserted"] == 3
    summary = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers).json()
    t = summary["totals"]
    assert t["impressions"] == 4200
    assert t["clicks"] == 190
    assert t["leads"] == 21
    assert t["spend"] == 370.0
    assert t["revenue"] == 1110.0
    # derived: ctr = 190/4200*100, cpl = 370/21, roas = 1110/370
    assert t["ctr"] == round(190 / 4200 * 100, 2)
    assert t["cpl"] == round(370 / 21, 2)
    assert t["roas"] == 3.0
    # by_platform has two channels; daily series spans two dates
    assert {r["platform"] for r in summary["by_platform"]} == {"instagram", "facebook"}
    assert len(summary["daily"]) == 2


def test_ingest_is_upsert(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _ingest(client, admin_headers, cid, [SEED[0]])
    # same (date, platform) again with new numbers → overwrites, no duplicate
    _ingest(client, admin_headers, cid, [{**SEED[0], "leads": 99, "spend": 999}])
    daily = client.get(f"{API}/clients/{cid}/analytics/daily", headers=admin_headers).json()
    assert daily["total"] == 1
    assert daily["items"][0]["leads"] == 99
    assert daily["items"][0]["spend"] == 999.0


def test_daily_date_and_platform_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _ingest(client, admin_headers, cid, SEED)
    only_ig = client.get(
        f"{API}/clients/{cid}/analytics/daily?platform=instagram", headers=admin_headers
    ).json()
    assert only_ig["total"] == 2
    day1 = client.get(
        f"{API}/clients/{cid}/analytics/daily?start=2026-09-01&end=2026-09-01",
        headers=admin_headers,
    ).json()
    assert day1["total"] == 2  # both platforms on 09-01


def test_summary_empty_client(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    summary = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers).json()
    assert summary["totals"]["impressions"] == 0
    assert summary["totals"]["ctr"] == 0.0  # no divide-by-zero
    assert summary["by_platform"] == []
    assert summary["daily"] == []


def test_ingest_requires_rows(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/analytics/ingest", headers=admin_headers, json={"rows": []}
    )
    assert resp.status_code == 422


def test_analytics_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    _ingest(client, admin_headers, cid_a, SEED)
    # client B sees none of A's data
    assert (
        client.get(f"{API}/clients/{cid_b}/analytics/daily", headers=admin_headers).json()["total"]
        == 0
    )


def test_assigned_user_can_read(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    _ingest(client, admin_headers, cid, SEED)
    assert (
        client.get(f"{API}/clients/{cid}/analytics/summary", headers=user_headers).status_code
        == 200
    )


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    assert (
        client.get(f"{API}/clients/{cid}/analytics/summary", headers=user_headers).status_code
        == 404
    )


def test_analytics_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/analytics/summary").status_code == 401
