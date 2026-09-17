"""API tests: the AI change-proposal engine (propose -> approve -> execute).

Covers the non-negotiable invariant the whole feature exists for — nothing is
written to the database before an explicit approve call — plus the
safety-critical edges: idempotent duplicate approval, whole-row staleness
conflicts, atomic rollback on a mid-execution failure, and rejection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError
from app.integrations.llm.base import ToolCall, ToolCallResponse
from app.integrations.llm.openrouter import OpenRouterClient
from app.models.plan import PlanTask
from app.models.user import User
from app.schemas.plan import PlanTaskCreate, PlanTaskUpdate
from app.services.proposal_service import ProposalService
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co.") -> uuid.UUID:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["client"]["id"])


def _create_chat(client, headers, cid) -> str:
    resp = client.post(f"{API}/clients/{cid}/assistant/chats", headers=headers, json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _scripted(responses: list[ToolCallResponse]):
    queue = list(responses)

    async def fake_complete_with_tools(
        self, *, messages, tools, tool_choice="auto", max_tokens=None, model=None, context=None
    ):
        return queue.pop(0)

    return fake_complete_with_tools


def _admin(db_session: Session) -> User:
    return db_session.query(User).filter_by(email="admin@test.com").one()


def test_command_turn_stages_then_approve_creates_task(
    client: TestClient, admin_headers: dict, monkeypatch
):
    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient,
        "complete_with_tools",
        _scripted(
            [
                ToolCallResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="propose_create_plan_task",
                            arguments={"title": "Q4 Launch"},
                        )
                    ],
                ),
                ToolCallResponse(content="I've drafted a new task for your review.", tool_calls=[]),
            ]
        ),
    )

    cid = _client_id(client, admin_headers)
    chat_id = _create_chat(client, admin_headers, cid)

    turn = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=admin_headers,
        json={"content": "Create a task called Q4 Launch"},
    )
    assert turn.status_code == 201, turn.text
    body = turn.json()
    assert body["proposal"] is not None
    proposal_id = body["proposal"]["id"]
    assert body["proposal"]["status"] == "pending_approval"
    ops = body["proposal"]["operations"]
    assert len(ops) == 1
    assert ops[0]["entity_type"] == "plan_task"
    assert ops[0]["operation_type"] == "create"
    assert ops[0]["field_changes"]["title"]["after"] == "Q4 Launch"

    # Nothing written yet — the whole point of the feature.
    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed.json()["total"] == 0

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal_id}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    approved = approve.json()
    assert approved["status"] == "completed"
    assert approved["operations"][0]["status"] == "executed"

    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["title"] == "Q4 Launch"

    audit = client.get(f"{API}/audit", headers=admin_headers, params={"proposal_id": proposal_id})
    assert audit.status_code == 200, audit.text
    assert audit.json()["total"] >= 1
    assert audit.json()["items"][0]["proposal_id"] == proposal_id

    # Reloading the chat (not just the live turn response) still surfaces the
    # proposal id, so the frontend can render the approval card after a
    # refresh — not only right after sending.
    reloaded = client.get(f"{API}/clients/{cid}/assistant/chats/{chat_id}", headers=admin_headers)
    assert reloaded.status_code == 200, reloaded.text
    assistant_messages = [m for m in reloaded.json()["messages"] if m["role"] == "assistant"]
    assert assistant_messages[-1]["proposal_id"] == proposal_id


def test_approve_is_idempotent_on_duplicate_call(
    client: TestClient, admin_headers: dict, db_session
):
    admin = _admin(db_session)
    cid = _client_id(client, admin_headers)
    svc = ProposalService(db_session)
    op = svc.stage_plan_task_create(cid, admin, PlanTaskCreate(title="Only once"))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="create a task",
        summary="create a task",
        operations=[op],
    )

    first = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    second = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["status"] == "completed"
    assert second.json()["status"] == "completed"

    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed.json()["total"] == 1  # not duplicated by the second approve


def test_staleness_conflict_fails_whole_proposal(
    client: TestClient, admin_headers: dict, db_session
):
    admin = _admin(db_session)
    cid = _client_id(client, admin_headers)
    created = client.post(
        f"{API}/clients/{cid}/plan/tasks", headers=admin_headers, json={"title": "Original"}
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    svc = ProposalService(db_session)
    op = svc.stage_plan_task_update(
        cid, admin, uuid.UUID(task_id), PlanTaskUpdate(title="AI Renamed")
    )
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="rename",
        summary="rename",
        operations=[op],
    )

    # Someone else edits the task before the proposal is approved. Bumped
    # `updated_at` explicitly (rather than a real PATCH) so the test is
    # deterministic — SQLite's `CURRENT_TIMESTAMP` is second-resolution, so a
    # real edit landing in the same wall-clock second as the create above
    # would otherwise leave `updated_at` textually unchanged.
    task = db_session.get(PlanTask, uuid.UUID(task_id))
    task.title = "Changed by someone else"
    task.updated_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.commit()

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == "failed"
    assert "changed since" in (body["error"] or "").lower()

    current = client.get(f"{API}/clients/{cid}/plan/tasks/{task_id}", headers=admin_headers)
    assert current.json()["title"] == "Changed by someone else"  # the AI's rename never applied


def test_multi_op_atomic_rollback_on_execute_time_failure(
    client: TestClient, admin_headers: dict, make_user, db_session
):
    """Client-assignment operations carry no ``updated_at`` snapshot to catch
    drift at the preflight staleness check, so a concurrent assignment made
    after staging is only caught when the operation actually replays at
    execute time — the scenario this test exercises, proving the *other* half
    of atomicity (a failure discovered mid-loop, not upfront)."""
    admin = _admin(db_session)
    member_json, _ = make_user(email="member2@test.com")
    cid = _client_id(client, admin_headers)

    svc = ProposalService(db_session)
    # seq 0: a plain create — will succeed in isolation.
    op1 = svc.stage_plan_task_create(cid, admin, PlanTaskCreate(title="Task A (from AI)"))
    # seq 1: assign a user who is not yet assigned — valid right now.
    op2 = svc.stage_assignment_assign(cid, admin, uuid.UUID(member_json["id"]))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="two changes",
        summary="two changes",
        operations=[op1, op2],
    )

    # Someone else assigns that same user to the client before this is approved.
    direct_assign = client.post(
        f"{API}/clients/{cid}/assignments",
        headers=admin_headers,
        json={"user_id": member_json["id"]},
    )
    assert direct_assign.status_code == 201, direct_assign.text

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == "failed"
    ops = sorted(body["operations"], key=lambda o: o["seq"])
    # op1 (seq 0) ran successfully in-memory but was rolled back with op2's failure.
    assert ops[0]["status"] == "skipped"
    assert ops[1]["status"] == "failed"
    assert "already assigned" in ops[1]["error"].lower()

    # Task A must not exist — its creation was undone atomically with op2's failure.
    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    titles = [t["title"] for t in listed.json()["items"]]
    assert "Task A (from AI)" not in titles


def test_reject_never_applies_and_blocks_later_approval(
    client: TestClient, admin_headers: dict, db_session
):
    admin = _admin(db_session)
    cid = _client_id(client, admin_headers)
    svc = ProposalService(db_session)
    op = svc.stage_plan_task_create(cid, admin, PlanTaskCreate(title="Should never exist"))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="x",
        summary="x",
        operations=[op],
    )

    rejected = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/reject",
        headers=admin_headers,
        json={"reason": "not needed"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "cancelled"

    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed.json()["total"] == 0

    # A cancelled proposal can never later be approved (claim requires pending_approval).
    late_approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert late_approve.status_code == 200
    assert late_approve.json()["status"] == "cancelled"
    listed_again = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed_again.json()["total"] == 0


def test_ambiguous_user_triggers_clarification_instead_of_guessing(
    client: TestClient, admin_headers: dict, make_user, monkeypatch
):
    user1, _ = make_user(email="john.smith@test.com", name="John Smith")
    user2, _ = make_user(email="john.doe@test.com", name="John Doe")

    cid = _client_id(client, admin_headers)
    for u in (user1, user2):
        resp = client.post(
            f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": u["id"]}
        )
        assert resp.status_code == 201, resp.text

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient,
        "complete_with_tools",
        _scripted(
            [
                ToolCallResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="c1", name="search_users", arguments={"query": "John"})
                    ],
                ),
                ToolCallResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="request_clarification",
                            arguments={
                                "question": (
                                    "Did you mean John Smith (john.smith@test.com) or "
                                    "John Doe (john.doe@test.com)?"
                                )
                            },
                        )
                    ],
                ),
            ]
        ),
    )

    chat_id = _create_chat(client, admin_headers, cid)
    turn = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=admin_headers,
        json={"content": "Assign John to this plan"},
    )
    assert turn.status_code == 201, turn.text
    body = turn.json()
    assert body["proposal"] is None
    assert "john.smith@test.com" in body["reply"]
    assert "john.doe@test.com" in body["reply"]


def test_non_admin_cannot_stage_a_client_assignment(
    client: TestClient, admin_headers: dict, make_user, db_session
):
    member_json, _ = make_user(email="member@test.com")
    target_json, _ = make_user(email="target@test.com")
    cid = _client_id(client, admin_headers)
    # The member needs client access for this to reach the admin-role check
    # (an inaccessible client 404s first, by design — that's not what this
    # test is about).
    assigned = client.post(
        f"{API}/clients/{cid}/assignments",
        headers=admin_headers,
        json={"user_id": member_json["id"]},
    )
    assert assigned.status_code == 201, assigned.text

    member = db_session.query(User).filter_by(email="member@test.com").one()
    svc = ProposalService(db_session)
    with pytest.raises(ForbiddenError):
        svc.stage_assignment_assign(cid, member, uuid.UUID(target_json["id"]))


def test_inaccessible_client_is_404_not_403_on_every_new_endpoint(
    client: TestClient, admin_headers: dict, make_user, db_session
):
    """Anti-IDOR house rule applied to the new surface: a user with no
    assignment to the client gets 404 (never 403, never a leak of whether the
    proposal/chat exists) on the turn, get, approve, and reject endpoints."""
    _outsider_json, outsider_headers = make_user(email="outsider@test.com")
    admin = _admin(db_session)
    cid = _client_id(client, admin_headers)

    svc = ProposalService(db_session)
    op = svc.stage_plan_task_create(cid, admin, PlanTaskCreate(title="Not this user's business"))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="x",
        summary="x",
        operations=[op],
    )
    chat_id = _create_chat(client, admin_headers, cid)

    turn = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=outsider_headers,
        json={"content": "anything"},
    )
    assert turn.status_code == 404, turn.text

    get_resp = client.get(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}", headers=outsider_headers
    )
    assert get_resp.status_code == 404, get_resp.text

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=outsider_headers
    )
    assert approve.status_code == 404, approve.text

    reject = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/reject",
        headers=outsider_headers,
        json={},
    )
    assert reject.status_code == 404, reject.text

    # Confirmed still pending — none of the outsider's calls touched anything.
    still_pending = client.get(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}", headers=admin_headers
    )
    assert still_pending.json()["status"] == "pending_approval"
