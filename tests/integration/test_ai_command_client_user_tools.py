"""API tests: Phase 2 of the AI change-proposal engine — client settings,
brand settings, and user-management tools.

The engine mechanics (atomicity, idempotency, whole-row staleness, rejection)
are already covered end-to-end against plan tasks in
``test_ai_command_proposals.py`` and are not re-tested per entity type here.
This file covers what's new: each Phase 2 tool's staging + execution, its
admin-only gate, and one full turn-endpoint round trip to catch any
registry/handler wiring mistake a service-level test wouldn't.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.ai.tools.registry import TOOLS
from app.core.exceptions import ForbiddenError
from app.integrations.llm.base import ToolCall, ToolCallResponse
from app.integrations.llm.openrouter import OpenRouterClient
from app.models.user import User
from app.schemas.client import ClientUpdate
from app.schemas.onboarding import BrandColorIn, BrandUpdate
from app.schemas.user import UserUpdate
from app.services.proposal_service import ProposalService
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co.") -> uuid.UUID:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["client"]["id"])


def _admin(db_session) -> User:
    return db_session.query(User).filter_by(email="admin@test.com").one()


def _scripted(responses: list[ToolCallResponse]):
    queue = list(responses)

    async def fake_complete_with_tools(
        self, *, messages, tools, tool_choice="auto", max_tokens=None, model=None, context=None
    ):
        return queue.pop(0)

    return fake_complete_with_tools


def test_no_access_management_tools_are_exposed_to_the_model():
    """Client isolation, hard boundary: nothing in this chat can grant/change/
    remove access to a client, or touch a user's account/role — that's handled
    in the agency dashboard, outside any single client's context (see
    ``app/ai/tools/registry.py``'s comment on this). Account creation was never
    exposed either — it needs a password, which has no safe path through the
    proposal engine's JSON payload (see ``ProposalService.stage_update_user``'s
    docstring). The underlying ``ProposalService`` methods still exist and are
    tested directly below/elsewhere — they're just never reachable from the
    model's tool list."""
    assert "propose_create_user" not in TOOLS
    assert "propose_update_user" not in TOOLS
    assert "propose_assign_user_to_client" not in TOOLS
    assert "propose_set_client_capabilities" not in TOOLS
    assert "propose_unassign_user_from_client" not in TOOLS
    assert "get_client_assignments" not in TOOLS


def test_propose_and_approve_client_update(client: TestClient, admin_headers: dict, db_session):
    admin = _admin(db_session)
    cid = _client_id(client, admin_headers)

    svc = ProposalService(db_session)
    op = svc.stage_update_client(
        cid, admin, ClientUpdate(name="Acme Renamed", website="https://acme-renamed.example")
    )
    assert op.field_changes["name"]["after"] == "Acme Renamed"
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="rename the client",
        summary="rename the client",
        operations=[op],
    )

    # Nothing written yet.
    unchanged = client.get(f"{API}/clients/{cid}", headers=admin_headers)
    assert unchanged.json()["name"] == "Acme Co."

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "completed"

    updated = client.get(f"{API}/clients/{cid}", headers=admin_headers)
    assert updated.json()["name"] == "Acme Renamed"
    assert updated.json()["website"] == "https://acme-renamed.example"


def test_non_admin_cannot_stage_client_update(
    client: TestClient, admin_headers: dict, make_user, db_session
):
    member_json, _ = make_user(email="member-client@test.com")
    cid = _client_id(client, admin_headers)
    assigned = client.post(
        f"{API}/clients/{cid}/assignments",
        headers=admin_headers,
        json={"user_id": member_json["id"]},
    )
    assert assigned.status_code == 201, assigned.text

    member = db_session.query(User).filter_by(email="member-client@test.com").one()
    svc = ProposalService(db_session)
    with pytest.raises(ForbiddenError):
        svc.stage_update_client(cid, member, ClientUpdate(name="Hijacked"))


def test_propose_and_approve_brand_update(client: TestClient, admin_headers: dict, db_session):
    admin = _admin(db_session)
    cid = _client_id(client, admin_headers)

    svc = ProposalService(db_session)
    op = svc.stage_update_brand(
        cid,
        admin,
        BrandUpdate(
            brand_voice="Bold, punchy, a little irreverent.",
            colors=[BrandColorIn(hex="#112233", label="Primary")],
            fonts=["Poppins"],
        ),
    )
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="update brand voice and colors",
        summary="update brand voice and colors",
        operations=[op],
    )

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "completed"

    updated = client.get(f"{API}/clients/{cid}", headers=admin_headers)
    body = updated.json()
    assert body["brand_voice"] == "Bold, punchy, a little irreverent."
    assert [c["hex"] for c in body["brand_colors"]] == ["#112233"]
    assert [f["family"] for f in body["brand_fonts"]] == ["Poppins"]


def test_propose_and_approve_user_role_change(
    client: TestClient, admin_headers: dict, make_user, db_session
):
    admin = _admin(db_session)
    member_json, _ = make_user(email="promote-me@test.com", role="user")
    cid = _client_id(client, admin_headers)

    svc = ProposalService(db_session)
    op = svc.stage_update_user(cid, admin, uuid.UUID(member_json["id"]), UserUpdate(role="manager"))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="promote to manager",
        summary="promote to manager",
        operations=[op],
    )

    # Nothing applied yet.
    still_user = client.get(f"{API}/users", headers=admin_headers).json()["items"]
    promoted = next(u for u in still_user if u["id"] == member_json["id"])
    assert promoted["role"] == "user"

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "completed"

    after = client.get(f"{API}/users", headers=admin_headers).json()["items"]
    promoted_after = next(u for u in after if u["id"] == member_json["id"])
    assert promoted_after["role"] == "manager"


def test_user_update_staleness_conflict_fails_the_proposal(
    client: TestClient, admin_headers: dict, make_user, db_session
):
    admin = _admin(db_session)
    member_json, _ = make_user(email="stale-target@test.com")
    cid = _client_id(client, admin_headers)

    svc = ProposalService(db_session)
    op = svc.stage_update_user(cid, admin, uuid.UUID(member_json["id"]), UserUpdate(role="manager"))
    proposal = svc.create_proposal(
        cid,
        chat_id=None,
        message_id=None,
        created_by=admin.id,
        raw_request="promote",
        summary="promote",
        operations=[op],
    )

    # Someone else edits the target user before this is approved. Bumped
    # `updated_at` explicitly (rather than relying on a real PATCH) so the
    # test is deterministic — SQLite's `CURRENT_TIMESTAMP` is second-
    # resolution, so an edit landing in the same wall-clock second as the
    # user's creation above would otherwise leave `updated_at` unchanged.
    target = db_session.get(User, uuid.UUID(member_json["id"]))
    target.name = "Renamed First"
    target.updated_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.commit()

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{proposal.id}/approve", headers=admin_headers
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == "failed"
    assert "changed since" in (body["error"] or "").lower()

    unchanged = client.get(f"{API}/users", headers=admin_headers).json()["items"]
    target = next(u for u in unchanged if u["id"] == member_json["id"])
    assert target["role"] == "user"  # the AI's promotion never applied


def test_turn_endpoint_end_to_end_for_propose_update_client(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """One full round trip through the command agent (not just ProposalService
    directly) to catch a mismatch between the tool's JSON-schema argument
    names and its handler's keyword arguments."""
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
                            name="propose_update_client",
                            arguments={"industry": "Outdoor & Recreation"},
                        )
                    ],
                ),
                ToolCallResponse(
                    content="I've drafted an industry update for review.", tool_calls=[]
                ),
            ]
        ),
    )

    cid = _client_id(client, admin_headers)
    chat = client.post(f"{API}/clients/{cid}/assistant/chats", headers=admin_headers, json={})
    assert chat.status_code == 201, chat.text
    chat_id = chat.json()["id"]

    turn = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn",
        headers=admin_headers,
        json={"content": "Change the industry to Outdoor & Recreation"},
    )
    assert turn.status_code == 201, turn.text
    body = turn.json()
    assert body["proposal"] is not None
    ops = body["proposal"]["operations"]
    assert len(ops) == 1
    assert ops[0]["entity_type"] == "client"
    assert ops[0]["field_changes"]["industry"]["after"] == "Outdoor & Recreation"

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{body['proposal']['id']}/approve",
        headers=admin_headers,
    )
    assert approve.status_code == 200, approve.text
    updated = client.get(f"{API}/clients/{cid}", headers=admin_headers)
    assert updated.json()["industry"] == "Outdoor & Recreation"
