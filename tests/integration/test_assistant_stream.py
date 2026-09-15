"""API tests: streamed ("Ask AI") project-assistant replies over SSE.

Covers the deterministic-fallback stream (AI provider unconfigured), the real
token-by-token path (monkeypatched ``OpenRouterClient.stream``), persistence of
the assembled reply, and access scoping (unassigned user → 404).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.integrations.llm.openrouter import OpenRouterClient
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, headers, name="Stream Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _chat_id(client, headers, cid):
    resp = client.post(f"{API}/clients/{cid}/assistant/chats", headers=headers, json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _events(body: str) -> list[dict]:
    events = []
    for frame in body.strip().split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:"):
            events.append(json.loads(frame[len("data:") :].strip()))
    return events


def test_stream_fallback_when_ai_unconfigured(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=admin_headers,
        json={"content": "What is this client's brand voice?"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _events(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "sources"
    assert "delta" in types
    assert types[-1] == "done"

    done = events[-1]
    assert done["content"]  # non-empty deterministic fallback
    assert done["message_id"]

    # The assembled reply is persisted after streaming (user + assistant turns).
    detail = client.get(f"{API}/clients/{cid}/assistant/chats/{chat}", headers=admin_headers).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][-1]["content"] == done["content"]


async def _fake_not_a_plan_complete(
    self, *, system, prompt, max_tokens=None, model=None, context=None
):
    """Every streamed turn first runs the plan-chat intent classifier (see
    AssistantService._maybe_handle_plan_request), which calls `.complete()` —
    mocked here to a deterministic "not a plan request" verdict so these
    stream-focused tests never make a real network call for that step."""
    return '{"wants_content_plan": false, "ready": false}'


def test_stream_emits_tokens_when_ai_configured(
    client: TestClient, admin_headers: dict, monkeypatch
):
    async def fake_stream(self, *, system, prompt, max_tokens=None, model=None, context=None):
        for token in ["Your ", "brand ", "voice ", "is ", "confident."]:
            yield token

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete", _fake_not_a_plan_complete)
    monkeypatch.setattr(OpenRouterClient, "stream", fake_stream)

    cid = _client_id(client, admin_headers, name="Configured Co.")
    chat = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=admin_headers,
        json={"content": "brand voice?"},
    )
    assert resp.status_code == 200, resp.text

    events = _events(resp.text)
    streamed = "".join(e["text"] for e in events if e["type"] == "delta")
    assert streamed == "Your brand voice is confident."
    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "Your brand voice is confident."


def test_stream_fallback_when_ai_configured_but_call_fails(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """Same distinction as the non-streaming path: a real mid-stream provider
    failure must not read as "AI responses aren't configured"."""

    async def fake_stream(self, *, system, prompt, max_tokens=None, model=None, context=None):
        raise RuntimeError("credit balance too low")
        yield  # pragma: no cover - unreachable, makes this an async generator

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete", _fake_not_a_plan_complete)
    monkeypatch.setattr(OpenRouterClient, "stream", fake_stream)

    cid = _client_id(client, admin_headers, name="Stream Error Co.")
    chat = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=admin_headers,
        json={"content": "brand voice?"},
    )
    assert resp.status_code == 200, resp.text

    events = _events(resp.text)
    done = events[-1]
    assert done["type"] == "done"
    assert "went wrong" in done["content"]
    assert "aren't configured" not in done["content"]
    assert "credit balance" not in done["content"]


def test_stream_delivers_a_clarifying_question_as_one_frame_not_animated(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """A content-plan request with no clear date range is delivered as a
    single delta + done (nothing to animate token-by-token), and the real
    conversational `.stream()` is never invoked for it."""

    async def fake_classify(self, *, system, prompt, max_tokens=None, model=None, context=None):
        return (
            '{"wants_content_plan": true, "ready": false, '
            '"clarifying_question": "What date range should this cover?"}'
        )

    async def fail_if_called(self, *, system, prompt, max_tokens=None, model=None, context=None):
        raise AssertionError("the conversational stream must not run for a plan-chat turn")

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete", fake_classify)
    monkeypatch.setattr(OpenRouterClient, "stream", fail_if_called)

    cid = _client_id(client, admin_headers, name="Clarify Co.")
    chat = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=admin_headers,
        json={"content": "Create some content for this client"},
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp.text)
    assert [e["type"] for e in events] == ["sources", "delta", "done"]
    assert events[1]["text"] == "What date range should this cover?"
    done = events[-1]
    assert done["content"] == "What date range should this cover?"
    assert done.get("action") is None


def test_stream_delivers_a_generated_plan_draft_as_an_action_frame(
    client: TestClient, admin_headers: dict, monkeypatch
):
    calls: list[str] = []

    async def fake_complete(self, *, system, prompt, max_tokens=None, model=None, context=None):
        calls.append(system)
        if len(calls) == 1:
            return (
                '{"wants_content_plan": true, "ready": true, '
                '"start_date": "2026-09-15", "end_date": "2026-09-20"}'
            )
        return json.dumps(
            {
                "items": [
                    {
                        "title": "Story: weekend hours",
                        "event_date": "2026-09-16",
                        "platform": "instagram",
                        "content_format": "story",
                        "category": "content",
                        "caption": "We're open this weekend!",
                        "hashtags": "",
                        "suggested_role": "content creator",
                    }
                ]
            }
        )

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete", fake_complete)

    cid = _client_id(client, admin_headers, name="Draft Stream Co.")
    chat = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=admin_headers,
        json={"content": "Create content from Sept 15 to Sept 20"},
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp.text)
    done = events[-1]
    assert done["type"] == "done"
    action = done["action"]
    assert action["type"] == "plan_draft"
    assert action["status"] == "pending"
    assert len(action["task_ids"]) == 1


def test_stream_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers, name="Private Co.")
    chat = _chat_id(client, admin_headers, cid)
    _, user_headers = make_user(role="user")  # not assigned to this client

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=user_headers,
        json={"content": "hi"},
    )
    assert resp.status_code == 404, resp.text
