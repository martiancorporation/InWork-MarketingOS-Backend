"""Tests: date-range bulk update/delete for the AI command layer
(app/ai/tools/handlers.py's propose_bulk_update_plan_tasks /
propose_bulk_delete_plan_tasks), and search_plan_tasks' range extension.

Exercises the handlers directly against a real DB session for the range/cap
edge cases (fast, precise), plus one full HTTP round trip proving a bulk tool
call lands as one ChangeProposal that approves and executes correctly.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.tools import handlers
from app.integrations.llm.base import ToolCall, ToolCallResponse
from app.integrations.llm.openrouter import OpenRouterClient
from app.models.enums import TaskStatus
from app.models.user import User
from app.schemas.plan import PlanTaskCreate
from app.services.plan_service import PlanService
from app.services.proposal_service import StagedBatch
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co.") -> uuid.UUID:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["client"]["id"])


def _admin(db_session: Session) -> User:
    return db_session.query(User).filter_by(email="admin@test.com").one()


def _make_task(db_session, cid, admin, title, day: date, status=TaskStatus.todo):
    plans = PlanService(db_session)
    return plans.create_task(
        cid,
        PlanTaskCreate(title=title, start_date=day, due_date=day, status=status),
        created_by=admin.id,
    )


def test_search_plan_tasks_range_returns_every_match_and_the_real_total(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    for day in (5, 10, 15, 20, 25):
        _make_task(db_session, cid, admin, f"Sept {day} post", date(2026, 9, day))
    _make_task(db_session, cid, admin, "October post", date(2026, 10, 1))

    result = handlers.search_plan_tasks(
        db_session, cid, admin, start_date="2026-09-01", end_date="2026-09-30", limit=3
    )
    assert result["total_matching"] == 5
    assert len(result["tasks"]) == 3  # capped by limit, total_matching is not


def test_search_plan_tasks_range_requires_both_edges():
    with pytest.raises(ValueError, match="both start_date and end_date"):
        handlers._resolve_date_window(on_date=None, start_date="2026-09-01", end_date=None)


def test_bulk_update_stages_every_task_in_range_as_one_batch(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    tasks = [
        _make_task(db_session, cid, admin, f"Sept {d} post", date(2026, 9, d)) for d in (5, 10, 15)
    ]
    _make_task(db_session, cid, admin, "October post (untouched)", date(2026, 10, 1))

    batch = handlers.propose_bulk_update_plan_tasks(
        db_session,
        cid,
        admin,
        start_date="2026-09-01",
        end_date="2026-09-30",
        new_status="done",
    )
    assert isinstance(batch, StagedBatch)
    assert len(batch.operations) == 3
    assert batch.skipped == []
    staged_ids = {op.entity_id for op in batch.operations}
    assert staged_ids == {t.id for t in tasks}
    for op in batch.operations:
        assert op.field_changes["status"]["after"] == "done"

    # Dry-run only — nothing actually changed yet.
    db_session.expire_all()
    fresh = PlanService(db_session).get_task(cid, tasks[0].id)
    assert fresh.status == TaskStatus.todo


def test_bulk_delete_stages_every_task_in_range(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    tasks = [
        _make_task(db_session, cid, admin, f"Sept {d} post", date(2026, 9, d)) for d in (5, 10)
    ]

    batch = handlers.propose_bulk_delete_plan_tasks(
        db_session, cid, admin, start_date="2026-09-01", end_date="2026-09-30"
    )
    assert len(batch.operations) == 2
    assert {op.entity_id for op in batch.operations} == {t.id for t in tasks}
    assert all(op.operation_type.value == "delete" for op in batch.operations)


def test_bulk_update_with_explicit_task_ids_ignores_date_range(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    a = _make_task(db_session, cid, admin, "A", date(2026, 9, 1))
    _make_task(db_session, cid, admin, "B", date(2026, 9, 2))

    batch = handlers.propose_bulk_update_plan_tasks(
        db_session, cid, admin, task_ids=[str(a.id)], priority="urgent"
    )
    assert len(batch.operations) == 1
    assert batch.operations[0].entity_id == a.id


def test_bulk_update_requires_at_least_one_field():
    with pytest.raises(ValueError, match="at least one field"):
        handlers.propose_bulk_update_plan_tasks(
            None, None, None, start_date="2026-09-01", end_date="2026-09-30"
        )


def test_bulk_op_requires_ids_or_a_full_range(client: TestClient, admin_headers: dict, db_session):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    with pytest.raises(ValueError, match="task_ids or both start_date and end_date"):
        handlers.propose_bulk_delete_plan_tasks(db_session, cid, admin)


def test_bulk_op_with_no_matches_is_a_clear_error(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    with pytest.raises(ValueError, match="No tasks matched"):
        handlers.propose_bulk_delete_plan_tasks(
            db_session, cid, admin, start_date="2026-09-01", end_date="2026-09-30"
        )


def test_bulk_op_over_the_cap_is_rejected(
    client: TestClient, admin_headers: dict, db_session: Session, monkeypatch
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    monkeypatch.setattr(handlers, "_MAX_BULK_MATCHES", 2)
    for d in (1, 2, 3):
        _make_task(db_session, cid, admin, f"Sept {d}", date(2026, 9, d))

    with pytest.raises(ValueError, match="bulk-operation limit"):
        handlers.propose_bulk_delete_plan_tasks(
            db_session, cid, admin, start_date="2026-09-01", end_date="2026-09-30"
        )


def test_bulk_update_skips_individually_failing_tasks_but_stages_the_rest(
    client: TestClient, admin_headers: dict, db_session: Session, monkeypatch
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    good = _make_task(db_session, cid, admin, "Good", date(2026, 9, 5))
    bad = _make_task(db_session, cid, admin, "Bad", date(2026, 9, 6))

    from app.core.exceptions import BadRequestError
    from app.services.proposal_service import ProposalService

    real_stage = ProposalService.stage_plan_task_update

    def flaky_stage(self, client_id, user, task_id, data):
        if task_id == bad.id:
            raise BadRequestError("simulated failure")
        return real_stage(self, client_id, user, task_id, data)

    monkeypatch.setattr(ProposalService, "stage_plan_task_update", flaky_stage)

    batch = handlers.propose_bulk_update_plan_tasks(
        db_session, cid, admin, task_ids=[str(good.id), str(bad.id)], new_status="done"
    )
    assert len(batch.operations) == 1
    assert batch.operations[0].entity_id == good.id
    assert len(batch.skipped) == 1
    assert batch.skipped[0]["task_id"] == str(bad.id)
    assert "simulated failure" in batch.skipped[0]["reason"]


def test_end_to_end_bulk_delete_via_turn_then_approve(
    client: TestClient, admin_headers: dict, db_session: Session, monkeypatch
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    for d in (10, 15, 20):
        _make_task(db_session, cid, admin, f"Sept {d} post", date(2026, 9, d))
    db_session.commit()

    chat = client.post(f"{API}/clients/{cid}/assistant/chats", headers=admin_headers, json={})
    chat_id = chat.json()["id"]

    queue = [
        ToolCallResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="propose_bulk_delete_plan_tasks",
                    arguments={"start_date": "2026-09-01", "end_date": "2026-09-30"},
                )
            ],
        ),
        ToolCallResponse(content="I've drafted that deletion for your review.", tool_calls=[]),
    ]

    async def fake_complete_with_tools(
        self, *, messages, tools, tool_choice="auto", max_tokens=None, model=None, context=None
    ):
        return queue.pop(0)

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete_with_tools", fake_complete_with_tools)

    turn = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=admin_headers,
        json={"content": "Delete the whole plan for September"},
    )
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["proposal"]
    assert proposal is not None
    assert len(proposal["operations"]) == 3
    assert all(op["operation_type"] == "delete" for op in proposal["operations"])

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal['id']}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "completed"

    remaining = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert remaining.json()["total"] == 0
