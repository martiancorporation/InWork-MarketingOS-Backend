"""API tests: automatic month-ahead content-plan generation sweep
(POST /automation/auto-plan-generation/run) — the scheduler job that drafts
next month's plan once a client's local day reaches the 15th."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auto_plan_generation_log import AutoPlanGenerationLog
from app.services.scheduler_service import _next_month_range
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co.") -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def test_sweep_generates_and_notifies(client, admin_headers: dict, db_session: Session, make_user):
    """Today (real clock) is on/after the 15th in this test environment, so a
    fresh active client should get next month's plan auto-drafted, with the
    dedupe log recorded and the assigned team notified."""
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )

    resp = client.post(f"{API}/automation/auto-plan-generation/run", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    row = next(r for r in data["details"] if r["client_id"] == cid)
    assert row["status"] == "generated"
    assert row["item_count"] > 0

    period, _start, _end = _next_month_range(date.today())
    assert row["period"] == period

    log = db_session.scalar(
        select(AutoPlanGenerationLog).where(AutoPlanGenerationLog.client_id == uuid.UUID(cid))
    )
    assert log is not None
    assert log.period == period

    notifications = client.get(f"{API}/notifications", headers=user_headers).json()
    assert any("content plan" in n["title"].lower() for n in notifications["items"])


def test_sweep_does_not_duplicate_on_a_second_run(client, admin_headers: dict, db_session: Session):
    cid = _client_id(client, admin_headers)

    first = client.post(f"{API}/automation/auto-plan-generation/run", headers=admin_headers)
    assert first.status_code == 200
    first_row = next(r for r in first.json()["details"] if r["client_id"] == cid)
    assert first_row["status"] == "generated"

    second = client.post(f"{API}/automation/auto-plan-generation/run", headers=admin_headers)
    assert second.status_code == 200
    second_row = next(r for r in second.json()["details"] if r["client_id"] == cid)
    assert second_row["status"] == "already_generated"

    rows = list(
        db_session.scalars(
            select(AutoPlanGenerationLog).where(AutoPlanGenerationLog.client_id == uuid.UUID(cid))
        )
    )
    assert len(rows) == 1  # never duplicated


def test_auto_plan_generation_sweep_is_admin_only(client, make_user):
    _user, headers = make_user()
    resp = client.post(f"{API}/automation/auto-plan-generation/run", headers=headers)
    assert resp.status_code == 403
