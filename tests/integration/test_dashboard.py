"""API tests: the AI dashboard (health/brief/watchdog/recommendations) + decisions.

The suite is hermetic (no ANTHROPIC_API_KEY), so these exercise the deterministic
fallback path and the decision write/merge. Signals are grounded in real client
data — including calendar posts pending approval.
"""

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


def test_dashboard_shape(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.get(f"{API}/clients/{cid}/dashboard", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # deterministic fallback ran (no API key in the test env)
    assert body["ai_generated"] is False

    hs = body["health_score"]
    assert 0 <= hs["score"] <= 100
    assert hs["band"] in {"excellent", "good", "attention", "critical"}
    assert len(hs["drivers"]) >= 1

    brief = body["executive_brief"]
    assert brief["headline"]
    assert brief["budget"]["pace"] in {"on-track", "ahead", "behind"}
    assert isinstance(brief["pending_actions"], list)

    assert isinstance(body["watchdog"], list) and len(body["watchdog"]) >= 1
    recs = body["recommendations"]
    assert len(recs) >= 1
    # a fresh client with no spend/leads gets the "launch first campaign" rec
    launch = next(r for r in recs if r["id"] == "rec-launch-first-campaign")
    # recommendation carries a structured expected-impact projection (item 5)
    assert launch["projection"] is not None
    assert launch["projection"]["metric"] == "leads"
    assert launch["projection"]["direction"] == "up"
    assert launch["projection"]["basis"]
    # nothing decided yet
    assert all(r["decision"] is None for r in recs)


def test_watchdog_reflects_pending_approvals(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    # create a calendar post — defaults to approval_status "pending"
    client.post(
        f"{API}/clients/{cid}/calendar/events",
        headers=admin_headers,
        json={
            "title": "Launch teaser",
            "type": "content",
            "platform": "instagram",
            "event_date": "2026-07-20",
            "event_time": "09:00:00",
        },
    )
    body = client.get(f"{API}/clients/{cid}/dashboard", headers=admin_headers).json()
    assert any(w["id"] == "w-approvals" for w in body["watchdog"])
    assert any("approval" in a.lower() for a in body["executive_brief"]["pending_actions"])


def test_record_and_merge_recommendation_decision(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    dec = client.post(
        f"{API}/clients/{cid}/recommendations/rec-refresh-creative/decision",
        headers=admin_headers,
        json={"decision": "accepted"},
    )
    assert dec.status_code == 201, dec.text
    assert dec.json()["decision"] == "accepted"
    assert dec.json()["rec_key"] == "rec-refresh-creative"

    # the dashboard now shows that recommendation as decided
    body = client.get(f"{API}/clients/{cid}/dashboard", headers=admin_headers).json()
    acted = next(r for r in body["recommendations"] if r["id"] == "rec-refresh-creative")
    assert acted["decision"]["decision"] == "accepted"


def test_reject_with_reason_and_history(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/recommendations/rec-refresh-creative/decision",
        headers=admin_headers,
        json={"decision": "rejected", "reason": "Creative is fresh this month."},
    )
    hist = client.get(f"{API}/clients/{cid}/recommendations/decisions", headers=admin_headers)
    assert hist.status_code == 200
    assert hist.json()["total"] == 1
    assert hist.json()["items"][0]["reason"] == "Creative is fresh this month."


def test_bad_decision_422(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/recommendations/rec-x/decision",
        headers=admin_headers,
        json={"decision": "maybe"},
    )
    assert resp.status_code == 422


def test_assigned_user_can_view_dashboard(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    assert client.get(f"{API}/clients/{cid}/dashboard", headers=user_headers).status_code == 200


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/dashboard", headers=user_headers).status_code == 404


def test_dashboard_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/dashboard").status_code == 401
