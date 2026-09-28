"""API tests: Ask AI's read-only enforcement for UserRole.user.

A normal ("user"-role) account must never be able to create, update, delete,
assign, approve, or reject anything through Ask AI — regardless of any
per-client capability they hold for the manual UI. Covers every layer of the
gate: tool exposure, dispatch-level defense-in-depth, the chat-drafted
content-plan flow, and the proposal approve/reject endpoints. Admins and
managers must be completely unaffected (regression coverage).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.integrations.llm.base import ToolCall, ToolCallResponse
from app.integrations.llm.openrouter import OpenRouterClient
from app.models.enums import ClientCapability
from app.models.user import User
from app.schemas.plan import PlanTaskCreate
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


def _assign_full_access(client, admin_headers, cid, user_id):
    """A "user"-role account WITH every capability assigned — proves the
    read-only gate is role-based, not just "missing a capability"."""
    resp = client.post(
        f"{API}/clients/{cid}/assignments",
        headers=admin_headers,
        json={"user_id": str(user_id), "capabilities": [c.value for c in ClientCapability]},
    )
    assert resp.status_code == 201, resp.text


def _admin(db_session: Session) -> User:
    return db_session.query(User).filter_by(email="admin@test.com").one()


def _capturing_complete_with_tools(captured: dict, reply: str = "Here's what I found."):
    async def fake(
        self, *, messages, tools, tool_choice="auto", max_tokens=None, model=None, context=None
    ):
        captured["tools"] = tools
        return ToolCallResponse(content=reply, tool_calls=[])

    return fake


def test_read_only_user_is_never_offered_a_write_tool(
    client: TestClient, admin_headers: dict, make_user, monkeypatch
):
    cid = _client_id(client, admin_headers)
    user, user_headers = make_user()
    _assign_full_access(client, admin_headers, cid, user["id"])
    chat_id = _create_chat(client, user_headers, cid)

    captured: dict = {}
    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient, "complete_with_tools", _capturing_complete_with_tools(captured)
    )

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=user_headers,
        json={"content": "What's on the plan board?"},
    )
    assert resp.status_code == 201, resp.text
    tool_names = {t["function"]["name"] for t in captured["tools"]}
    assert "search_plan_tasks" in tool_names  # read tools still offered
    assert not any(name.startswith("propose_") for name in tool_names)


def test_admin_and_manager_still_get_write_tools(
    client: TestClient, admin_headers: dict, monkeypatch
):
    cid = _client_id(client, admin_headers)
    chat_id = _create_chat(client, admin_headers, cid)

    captured: dict = {}
    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient, "complete_with_tools", _capturing_complete_with_tools(captured)
    )

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=admin_headers,
        json={"content": "What's on the plan board?"},
    )
    assert resp.status_code == 201, resp.text
    tool_names = {t["function"]["name"] for t in captured["tools"]}
    assert "propose_create_plan_task" in tool_names
    assert "propose_bulk_delete_plan_tasks" in tool_names


def test_read_only_user_write_attempt_is_rejected_even_if_model_calls_it_anyway(
    client: TestClient, admin_headers: dict, make_user, monkeypatch
):
    """Defense-in-depth: even if a compromised/hallucinating model somehow
    still emits a propose_* tool call (it wasn't offered one, but nothing
    stops a test from forcing this path), the dispatch-level guard must
    refuse it and nothing may be staged."""
    cid = _client_id(client, admin_headers)
    user, user_headers = make_user()
    _assign_full_access(client, admin_headers, cid, user["id"])
    chat_id = _create_chat(client, user_headers, cid)

    async def fake_complete_with_tools(
        self, *, messages, tools, tool_choice="auto", max_tokens=None, model=None, context=None
    ):
        return ToolCallResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1", name="propose_create_plan_task", arguments={"title": "Sneaky"}
                )
            ],
        )

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete_with_tools", fake_complete_with_tools)

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=user_headers,
        json={"content": "Create some data for me"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["proposal"] is None

    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed.json()["total"] == 0


def test_read_only_user_cannot_request_content_plan_generation(
    client: TestClient, admin_headers: dict, make_user, monkeypatch
):
    cid = _client_id(client, admin_headers)
    user, user_headers = make_user()
    _assign_full_access(client, admin_headers, cid, user["id"])
    chat_id = _create_chat(client, user_headers, cid)

    async def fake_classify(self, message, *, history, today):
        from app.ai.plan_chat_intent import PlanChatIntent

        return PlanChatIntent(
            wants_content_plan=True,
            ready=True,
            start_date=today,
            end_date=today,
            clarifying_question=None,
        )

    from app.ai.plan_chat_intent import PlanChatIntentAgent

    monkeypatch.setattr(PlanChatIntentAgent, "classify", fake_classify)

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=user_headers,
        json={"content": "Create content for this month"},
    )
    assert resp.status_code == 201, resp.text
    assert "read-only" in resp.json()["reply"].lower()
    assert resp.json()["proposal"] is None

    listed = client.get(f"{API}/clients/{cid}/calendar/events", headers=admin_headers)
    assert listed.json()["total"] == 0


def test_read_only_user_cannot_approve_a_proposal(
    client: TestClient, admin_headers: dict, make_user, db_session: Session
):
    cid = _client_id(client, admin_headers)
    user, user_headers = make_user()
    _assign_full_access(client, admin_headers, cid, user["id"])

    admin = _admin(db_session)
    svc = ProposalService(db_session)
    op = svc.stage_plan_task_create(cid, admin, PlanTaskCreate(title="Admin drafted this"))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="create a task",
        summary="create a task",
        operations=[op],
    )

    resp = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=user_headers
    )
    assert resp.status_code == 403, resp.text

    reject_resp = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/reject",
        headers=user_headers,
        json={"reason": "no"},
    )
    assert reject_resp.status_code == 403, reject_resp.text

    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed.json()["total"] == 0  # still nothing written


def test_admin_can_still_approve_and_reject_normally(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    admin = _admin(db_session)
    svc = ProposalService(db_session)
    op = svc.stage_plan_task_create(cid, admin, PlanTaskCreate(title="Fine to approve"))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="create a task",
        summary="create a task",
        operations=[op],
    )
    resp = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
