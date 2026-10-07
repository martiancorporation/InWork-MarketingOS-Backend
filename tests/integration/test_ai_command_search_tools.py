"""Tests for `search_plan_tasks`'s `on_date` filter.

Regression coverage for a real gap: the tool had no way to resolve "the plan
for the 26th" to an existing task at all (only a title-substring `query`),
which is a direct, confirmed cause of the AI creating a duplicate task
instead of finding and updating the one the user meant — see command_agent's
system prompt and this tool's description for the behavioral fix that
depends on this parameter actually working.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.tools import handlers
from app.models.user import User
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co.") -> uuid.UUID:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["client"]["id"])


def _create_task(client, admin_headers, cid, **overrides) -> str:
    payload = {"title": "Untitled", "category": "content", "status": "todo", **overrides}
    resp = client.post(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _admin(db_session: Session) -> User:
    return db_session.query(User).filter_by(email="admin@test.com").one()


def test_on_date_finds_the_task_scheduled_that_day(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    target = _create_task(client, admin_headers, cid, title="Launch post", due_date="2026-09-26")
    _create_task(client, admin_headers, cid, title="Unrelated post", due_date="2026-09-27")

    result = handlers.search_plan_tasks(db_session, cid, _admin(db_session), on_date="2026-09-26")

    ids = [t["id"] for t in result["tasks"]]
    assert target in ids
    assert len(result["tasks"]) == 1


def test_on_date_matches_a_task_whose_span_includes_the_date(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """A multi-day task (start_date through due_date) must match a query for
    any date inside that span, not just its exact edges — same overlap
    semantics the calendar already relies on."""
    cid = _client_id(client, admin_headers)
    target = _create_task(
        client,
        admin_headers,
        cid,
        title="Week-long campaign",
        start_date="2026-09-24",
        due_date="2026-09-28",
    )

    result = handlers.search_plan_tasks(db_session, cid, _admin(db_session), on_date="2026-09-26")

    assert [t["id"] for t in result["tasks"]] == [target]


def test_on_date_excludes_undated_tasks(client: TestClient, admin_headers: dict, db_session: Session):
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, title="Someday task")  # no dates at all
    _create_task(client, admin_headers, cid, title="The 26th task", due_date="2026-09-26")

    result = handlers.search_plan_tasks(db_session, cid, _admin(db_session), on_date="2026-09-26")

    titles = [t["title"] for t in result["tasks"]]
    assert titles == ["The 26th task"]


def test_on_date_with_no_match_returns_empty_not_everything(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, title="Some other day", due_date="2026-09-01")

    result = handlers.search_plan_tasks(db_session, cid, _admin(db_session), on_date="2026-09-26")

    assert result["tasks"] == []
    assert result["total_matching"] == 0


def test_no_filters_still_returns_undated_tasks(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """Plain browsing (no query, no on_date) must keep behaving exactly as
    before — undated tasks still show up."""
    cid = _client_id(client, admin_headers)
    _create_task(client, admin_headers, cid, title="Undated task")

    result = handlers.search_plan_tasks(db_session, cid, _admin(db_session))

    assert any(t["title"] == "Undated task" for t in result["tasks"])
