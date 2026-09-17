"""API tests: the AI command layer's streamed turn endpoint (SSE).

Covers the deterministic "not configured" reply, real token-by-token text
streaming, and a tool-calling round whose accumulated tool call becomes a
staged ``ChangeProposal`` on the final ``done`` frame — mirroring
``test_assistant_stream.py``'s coverage of the ask-flow's SSE endpoint, for
the command layer's ``/turn/stream`` instead.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.integrations.llm.base import StreamDelta, ToolCall
from app.integrations.llm.openrouter import OpenRouterClient
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, headers, name="Stream Cmd Co.") -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _chat_id(client, headers, cid) -> str:
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


def _scripted_stream(rounds: list[list[StreamDelta]]):
    """Each call to ``stream_with_tools`` (one per agent round) pops the next
    scripted round and yields its events — mirrors the plain ``fake_stream``
    pattern in ``test_assistant_stream.py``, extended for multiple rounds."""
    queue = list(rounds)

    async def fake_stream_with_tools(
        self, *, messages, tools, tool_choice="auto", max_tokens=None, model=None, context=None
    ):
        for event in queue.pop(0):
            yield event

    return fake_stream_with_tools


def test_turn_stream_fallback_when_ai_unconfigured(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    chat_id = _chat_id(client, admin_headers, cid)

    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn/stream",
        headers=admin_headers,
        json={"content": "Create a task called Q4 Launch"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _events(resp.text)
    assert [e["type"] for e in events] == ["done"]
    assert events[0]["proposal"] is None
    assert "isn't configured" in events[0]["content"].lower()


def test_turn_stream_emits_real_text_deltas_with_no_proposal(
    client: TestClient, admin_headers: dict, monkeypatch
):
    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient,
        "stream_with_tools",
        _scripted_stream(
            [[StreamDelta(text="Here "), StreamDelta(text="are "), StreamDelta(text="your tasks.")]]
        ),
    )

    cid = _client_id(client, admin_headers, name="Stream Cmd Q&A Co.")
    chat_id = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn/stream",
        headers=admin_headers,
        json={"content": "Summarize the plan board"},
    )
    assert resp.status_code == 200, resp.text

    events = _events(resp.text)
    assert [e["type"] for e in events] == ["delta", "delta", "delta", "done"]
    streamed = "".join(e["text"] for e in events if e["type"] == "delta")
    assert streamed == "Here are your tasks."
    done = events[-1]
    assert done["content"] == "Here are your tasks."
    assert done["proposal"] is None

    # Persisted like any other turn.
    detail = client.get(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}", headers=admin_headers
    ).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][-1]["content"] == "Here are your tasks."
    assert detail["messages"][-1]["proposal_id"] is None


def test_turn_stream_tool_progress_then_proposal_on_done(
    client: TestClient, admin_headers: dict, monkeypatch
):
    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient,
        "stream_with_tools",
        _scripted_stream(
            [
                [
                    StreamDelta(
                        tool_call=ToolCall(
                            id="call_1",
                            name="propose_create_plan_task",
                            arguments={"title": "Q4 Launch"},
                        )
                    )
                ],
                [StreamDelta(text="I've "), StreamDelta(text="drafted that task for review.")],
            ]
        ),
    )

    cid = _client_id(client, admin_headers, name="Stream Cmd Write Co.")
    chat_id = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}/turn/stream",
        headers=admin_headers,
        json={"content": "Create a task called Q4 Launch"},
    )
    assert resp.status_code == 200, resp.text

    events = _events(resp.text)
    assert events[0] == {"type": "tool_progress", "label": "Drafting a new task"}
    deltas = [e for e in events if e["type"] == "delta"]
    assert "".join(e["text"] for e in deltas) == "I've drafted that task for review."
    done = events[-1]
    assert done["type"] == "done"
    assert done["content"] == "I've drafted that task for review."
    assert done["proposal"] is not None
    assert done["proposal"]["status"] == "pending_approval"
    ops = done["proposal"]["operations"]
    assert len(ops) == 1
    assert ops[0]["entity_type"] == "plan_task"
    assert ops[0]["field_changes"]["title"]["after"] == "Q4 Launch"

    # Nothing written yet — same invariant as the non-streamed turn endpoint.
    listed = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed.json()["total"] == 0

    # Reloading the chat surfaces the same proposal id (see AssistantMessageRead.proposal_id).
    detail = client.get(
        f"{API}/clients/{cid}/assistant/chats/{chat_id}", headers=admin_headers
    ).json()
    assert detail["messages"][-1]["proposal_id"] == done["proposal"]["id"]

    approve = client.post(
        f"{API}/clients/{cid}/assistant/proposals/{done['proposal']['id']}/approve",
        headers=admin_headers,
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "completed"
    listed_after = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert listed_after.json()["total"] == 1
    assert listed_after.json()["items"][0]["title"] == "Q4 Launch"
