"""API tests: Project AI assistant ("Ask AI about this project").

Covers chat CRUD, the ask flow (deterministic fallback when Claude is unconfigured
+ the real AI path via a monkeypatched client), message ordering, and RBAC
(unassigned user → 404, unauthenticated → 401).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.integrations.anthropic.client import AnthropicClient
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _new_chat(client, headers, cid, **body):
    resp = client.post(f"{API}/clients/{cid}/assistant/chats", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- chat CRUD ----


def test_create_chat_defaults(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid, title="Q3 planning")
    assert chat["title"] == "Q3 planning"
    assert chat["context_type"] == "project"  # default


def test_ask_fallback_when_ai_unconfigured(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages",
        headers=admin_headers,
        json={"content": "What is this client's brand voice?"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"]  # non-empty deterministic fallback
    assert "sources" in body


def test_ask_uses_ai_when_configured(client: TestClient, admin_headers: dict, monkeypatch):
    async def fake_complete(self, *, system, prompt, max_tokens=None, context=None):
        assert "brand voice" in prompt  # the question flows into the prompt
        return "Your brand voice is confident and warm."

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "complete", fake_complete)

    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages",
        headers=admin_headers,
        json={"content": "Describe the brand voice."},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["message"]["content"] == "Your brand voice is confident and warm."


def test_chat_detail_orders_messages(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)
    client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages",
        headers=admin_headers,
        json={"content": "First question"},
    )
    detail = client.get(f"{API}/clients/{cid}/assistant/chats/{chat['id']}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    msgs = detail.json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "First question"
    assert msgs[1]["role"] == "assistant"


def test_list_chats(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _new_chat(client, admin_headers, cid, title="A")
    _new_chat(client, admin_headers, cid, title="B")
    resp = client.get(f"{API}/clients/{cid}/assistant/chats", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 2
    assert {"items", "total", "page", "page_size"} <= body.keys()


def test_delete_chat(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    chat = _new_chat(client, admin_headers, cid)
    assert (
        client.delete(
            f"{API}/clients/{cid}/assistant/chats/{chat['id']}", headers=admin_headers
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"{API}/clients/{cid}/assistant/chats/{chat['id']}", headers=admin_headers
        ).status_code
        == 404
    )


# ---- RBAC ----


def test_assigned_user_can_use_assistant(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    chat = _new_chat(client, user_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat['id']}/messages",
        headers=user_headers,
        json={"content": "hi"},
    )
    assert resp.status_code == 201, resp.text


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    assert (
        client.get(f"{API}/clients/{cid}/assistant/chats", headers=user_headers).status_code == 404
    )


def test_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/assistant/chats").status_code == 401
