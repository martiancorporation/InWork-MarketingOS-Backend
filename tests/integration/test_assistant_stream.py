"""API tests: streamed ("Ask AI") project-assistant replies over SSE.

Covers the deterministic-fallback stream (Claude unconfigured), the real
token-by-token path (monkeypatched ``AnthropicClient.stream``), persistence of
the assembled reply, and access scoping (unassigned user → 404).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.integrations.anthropic.client import AnthropicClient
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, headers, name="Stream Co."):
    resp = client.post(f"{API}/clients/onboarding", headers=headers, json=onboarding_payload(name=name))
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


def test_stream_emits_tokens_when_ai_configured(
    client: TestClient, admin_headers: dict, monkeypatch
):
    async def fake_stream(self, *, system, prompt, max_tokens=None, context=None):
        for token in ["Your ", "brand ", "voice ", "is ", "confident."]:
            yield token

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "stream", fake_stream)

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


def test_stream_unassigned_user_gets_404(
    client: TestClient, admin_headers: dict, make_user
):
    cid = _client_id(client, admin_headers, name="Private Co.")
    chat = _chat_id(client, admin_headers, cid)
    _, user_headers = make_user(role="user")  # not assigned to this client

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=user_headers,
        json={"content": "hi"},
    )
    assert resp.status_code == 404, resp.text
