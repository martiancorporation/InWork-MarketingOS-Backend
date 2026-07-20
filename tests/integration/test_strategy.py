"""API tests: strategy-adherence tracking (BE-06)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import TaskStatus
from app.models.plan import PlanTask
from tests.conftest import API
from tests.helpers import onboarding_payload


def _cid(client: TestClient, admin_headers: dict, name: str = "Acme Co.") -> str:
    r = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert r.status_code == 201, r.text
    return r.json()["client"]["id"]


def _put_strategy(client, headers, cid, content="Focus Q3 on lead-gen.", title="Q3 plan"):
    return client.put(
        f"{API}/clients/{cid}/strategy",
        headers=headers,
        json={"title": title, "content": content},
    )


# ---- record + read ---- #


def test_set_and_get_current_strategy(client, admin_headers):
    cid = _cid(client, admin_headers)
    r1 = _put_strategy(client, admin_headers, cid, content="v1")
    assert r1.status_code == 201, r1.text
    assert r1.json()["version"] == 1

    r2 = _put_strategy(client, admin_headers, cid, content="v2")
    assert r2.status_code == 201
    assert r2.json()["version"] == 2

    got = client.get(f"{API}/clients/{cid}/strategy", headers=admin_headers)
    assert got.status_code == 200
    assert got.json()["version"] == 2
    assert got.json()["content"] == "v2"


def test_get_strategy_none_recorded_404(client, admin_headers):
    cid = _cid(client, admin_headers)
    r = client.get(f"{API}/clients/{cid}/strategy", headers=admin_headers)
    assert r.status_code == 404


def test_set_strategy_empty_content_422(client, admin_headers):
    cid = _cid(client, admin_headers)
    r = client.put(f"{API}/clients/{cid}/strategy", headers=admin_headers, json={"content": ""})
    assert r.status_code == 422


def test_set_strategy_extra_field_422(client, admin_headers):
    cid = _cid(client, admin_headers)
    r = client.put(
        f"{API}/clients/{cid}/strategy",
        headers=admin_headers,
        json={"content": "ok", "bogus": 1},
    )
    assert r.status_code == 422


# ---- adherence ---- #


def test_adherence_no_signals(client, admin_headers):
    cid = _cid(client, admin_headers)
    r = client.get(f"{API}/clients/{cid}/strategy/adherence", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_strategy"] is False
    assert body["total_recommendations"] == 0
    assert body["decision_adherence"] is None
    assert body["task_completion"] is None
    assert body["adherence_score"] is None
    assert body["basis"] == []


def test_adherence_blends_decisions_and_tasks(client, admin_headers, db_session: Session):
    cid = _cid(client, admin_headers)
    _put_strategy(client, admin_headers, cid, content="the plan")

    # Recommendation decisions: 1 accepted, 1 modified, 1 rejected → (1 + 0.5)/3 = 0.5
    for rec_key, decision in (("rec-a", "accepted"), ("rec-b", "modified"), ("rec-c", "rejected")):
        rd = client.post(
            f"{API}/clients/{cid}/recommendations/{rec_key}/decision",
            headers=admin_headers,
            json={"decision": decision},
        )
        assert rd.status_code == 201, rd.text

    # Plan tasks: 2 done of 4 → 0.5
    c = uuid.UUID(cid)
    for i in range(2):
        db_session.add(PlanTask(client_id=c, title=f"done {i}", status=TaskStatus.done))
    for i in range(2):
        db_session.add(PlanTask(client_id=c, title=f"open {i}", status=TaskStatus.todo))
    db_session.commit()

    r = client.get(f"{API}/clients/{cid}/strategy/adherence", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_strategy"] is True
    assert body["current_version"] == 1
    assert (body["accepted"], body["modified"], body["rejected"]) == (1, 1, 1)
    assert body["decision_adherence"] == 0.5
    assert body["tasks_done"] == 2
    assert body["tasks_total"] == 4
    assert body["task_completion"] == 0.5
    assert body["adherence_score"] == 50
    assert set(body["basis"]) == {"recommendation_decisions", "task_completion"}


# ---- authz ---- #


def test_set_strategy_inaccessible_client_404(client, admin_headers, make_user):
    _user, uh = make_user()
    cid = _cid(client, admin_headers)  # user not assigned
    r = _put_strategy(client, uh, cid)
    assert r.status_code == 404


def test_adherence_inaccessible_client_404(client, admin_headers, make_user):
    _user, uh = make_user()
    cid = _cid(client, admin_headers)
    r = client.get(f"{API}/clients/{cid}/strategy/adherence", headers=uh)
    assert r.status_code == 404


def test_strategy_requires_auth_401(client, admin_headers):
    cid = _cid(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/strategy").status_code == 401
