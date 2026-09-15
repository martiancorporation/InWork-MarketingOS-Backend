"""API tests: conversational content-plan creation inside Ask AI —
- the intent classifier gating a normal chat reply vs. a clarifying question
  vs. an actual draft
- the chat card's approve/reject actions reusing the plan-generation machinery
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.integrations.llm.openrouter import OpenRouterClient
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co.") -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _new_chat(client, headers, cid) -> dict:
    resp = client.post(f"{API}/clients/{cid}/assistant/chats", headers=headers, json={})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ask(client, headers, cid, chat_id, content: str):
    return client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/messages",
        headers=headers,
        json={"content": content},
    )


def _mock_sequential_completions(monkeypatch, responses: list[str]):
    """Each call to OpenRouterClient.complete returns the next response in
    order — the classifier always runs first, so ``responses[0]`` is its
    answer and ``responses[1]`` (if present) is the plan-generation call."""
    calls: list[str] = []

    async def fake_complete(self, *, system, prompt, max_tokens=None, model=None, context=None):
        calls.append(system)
        return responses[len(calls) - 1]

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete", fake_complete)
    return calls


_NOT_A_PLAN = json.dumps({"wants_content_plan": False, "ready": False})


def test_ordinary_question_is_unaffected_by_the_intent_check(
    client: TestClient, admin_headers: dict, monkeypatch
):
    calls = _mock_sequential_completions(
        monkeypatch, [_NOT_A_PLAN, "Your brand voice is friendly and direct."]
    )
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)

    resp = _ask(client, admin_headers, cid, chat["id"], "What is this client's brand voice?")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["message"]["content"] == "Your brand voice is friendly and direct."
    assert body["message"]["action"] is None
    assert len(calls) == 2  # classifier, then the real conversational answer


def test_ambiguous_plan_request_asks_a_clarifying_question(
    client: TestClient, admin_headers: dict, monkeypatch
):
    clarify = json.dumps(
        {
            "wants_content_plan": True,
            "ready": False,
            "clarifying_question": "Sure — what date range should this cover?",
        }
    )
    calls = _mock_sequential_completions(monkeypatch, [clarify])
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)

    resp = _ask(client, admin_headers, cid, chat["id"], "Create some content for this client")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["message"]["content"] == "Sure — what date range should this cover?"
    assert body["message"]["action"] is None
    # Only the classifier ran — the conversational agent is never invoked for
    # a message that turned out to want a content plan.
    assert len(calls) == 1


def test_clear_plan_request_generates_a_draft_card(
    client: TestClient, admin_headers: dict, monkeypatch
):
    ready = json.dumps(
        {
            "wants_content_plan": True,
            "ready": True,
            "start_date": "2026-09-15",
            "end_date": "2026-09-20",
        }
    )
    generation = json.dumps(
        {
            "items": [
                {
                    "title": "Carousel: fall specials",
                    "event_date": "2026-09-16",
                    "platform": "instagram",
                    "content_format": "carousel",
                    "category": "content",
                    "caption": "Check out our fall specials!",
                    "hashtags": "#fall",
                    "suggested_role": "content creator",
                }
            ]
        }
    )
    _mock_sequential_completions(monkeypatch, [ready, generation])
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)

    resp = _ask(client, admin_headers, cid, chat["id"], "Create content from Sept 15 to Sept 20")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    action = body["message"]["action"]
    assert action is not None
    assert action["type"] == "plan_draft"
    assert action["status"] == "pending"
    assert action["start_date"] == "2026-09-15"
    assert action["end_date"] == "2026-09-20"
    assert len(action["task_ids"]) == 1
    assert action["items"][0]["title"] == "Carousel: fall specials"

    # The drafted item is a real row, immediately visible on the Plan board.
    tasks = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers).json()
    assert any(t["id"] == action["task_ids"][0] for t in tasks["items"])


def _generate_draft(client, admin_headers, cid, chat_id, monkeypatch) -> dict:
    ready = json.dumps(
        {
            "wants_content_plan": True,
            "ready": True,
            "start_date": "2026-09-15",
            "end_date": "2026-09-20",
        }
    )
    generation = json.dumps(
        {
            "items": [
                {
                    "title": "Reel: new arrivals",
                    "event_date": "2026-09-17",
                    "platform": "instagram",
                    "content_format": "reel",
                    "category": "content",
                    "caption": "New arrivals this week.",
                    "hashtags": "#new",
                    "suggested_role": "content creator",
                }
            ]
        }
    )
    _mock_sequential_completions(monkeypatch, [ready, generation])
    resp = _ask(client, admin_headers, cid, chat_id, "Create content Sept 15-20")
    assert resp.status_code == 201, resp.text
    return resp.json()["message"]


def test_approve_plan_draft_approves_the_underlying_items(
    client: TestClient, admin_headers: dict, monkeypatch
):
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)
    message = _generate_draft(client, admin_headers, cid, chat["id"], monkeypatch)

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages/{message['id']}/approve-plan",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["action"]["status"] == "approved"

    task_id = message["action"]["task_ids"][0]
    task = client.get(f"{API}/clients/{cid}/plan/tasks/{task_id}", headers=admin_headers).json()
    # approve_batch doesn't assign anyone, but it does approve the linked event.
    assert task["status"] == "todo"

    # Resolved — a second approve attempt is rejected.
    again = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages/{message['id']}/approve-plan",
        headers=admin_headers,
    )
    assert again.status_code == 400


def test_reject_plan_draft_blocks_the_underlying_items(
    client: TestClient, admin_headers: dict, monkeypatch
):
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)
    message = _generate_draft(client, admin_headers, cid, chat["id"], monkeypatch)

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages/{message['id']}/reject-plan",
        headers=admin_headers,
        json={"reason": "not relevant right now"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["action"]["status"] == "rejected"

    task_id = message["action"]["task_ids"][0]
    task = client.get(f"{API}/clients/{cid}/plan/tasks/{task_id}", headers=admin_headers).json()
    assert task["status"] == "blocked"


def test_approve_plan_draft_requires_manage_calendar_capability(
    client: TestClient, admin_headers: dict, monkeypatch, make_user
):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments",
        headers=admin_headers,
        json={"user_id": user["id"], "capabilities": ["review_results"]},
    )
    chat = _new_chat(client, admin_headers, cid)
    message = _generate_draft(client, admin_headers, cid, chat["id"], monkeypatch)

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages/{message['id']}/approve-plan",
        headers=user_headers,
    )
    assert resp.status_code == 403


def test_approve_plan_on_a_message_without_an_action_is_404(
    client: TestClient, admin_headers: dict
):
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)
    resp = _ask(client, admin_headers, cid, chat["id"], "hello")
    message_id = resp.json()["message"]["id"]

    approve = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages/{message_id}/approve-plan",
        headers=admin_headers,
    )
    assert approve.status_code == 404
