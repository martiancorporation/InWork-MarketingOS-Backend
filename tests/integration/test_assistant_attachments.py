"""Ask AI file & image attachments.

The composer used to discard the file bytes and append the *filenames* to the
message text, so "why doesn't this report match?" reached the model as a filename.
These tests pin the real contract: bytes reach Claude (images as vision blocks,
documents as extracted text), the reference is owner-scoped, and the attachment
survives a chat reload with a freshly signed URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_storage
from app.integrations.anthropic.client import AnthropicClient
from app.main import app
from tests.conftest import API
from tests.helpers import onboarding_payload

# The upload route needs python-multipart; skip cleanly if it's absent.
multipart = pytest.importorskip("multipart", reason="python-multipart not installed")

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-pixels" * 4
CSV = b"date,leads,spend\n2026-07-01,12,340.50\n2026-07-02,9,301.00\n"


class FakeStorage:
    """In-memory ``Storage`` that keeps the bytes, so reads round-trip."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.is_configured = True
        self.signed: list[str] = []

    def upload(self, fileobj, key, content_type):
        self.objects[key] = fileobj.read()

    def download(self, key):
        return self.objects[key]

    def generate_download_url(self, key, expiry_seconds):
        self.signed.append(key)
        return f"https://s3.example.test/{key}?signed=1&expires={expiry_seconds}"

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def storage() -> Generator[FakeStorage, None, None]:
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
def captured(monkeypatch) -> dict:
    """Configure Claude and record what the vision / text calls received."""
    seen: dict = {}

    async def fake_complete(self, *, system, prompt, max_tokens=None, context=None):
        seen["prompt"] = prompt
        seen["system"] = system
        seen["images"] = []
        return "Text answer."

    async def fake_complete_with_images(
        self, *, system, prompt, images, max_tokens=None, context=None, operation=None
    ):
        seen["prompt"] = prompt
        seen["system"] = system
        seen["images"] = images
        return "I can see the chart."

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "complete", fake_complete)
    monkeypatch.setattr(AnthropicClient, "complete_with_images", fake_complete_with_images)
    return seen


def _client_id(client: TestClient, admin_headers: dict) -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name="Acme Co.")
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _chat_id(client: TestClient, admin_headers: dict, cid: str) -> str:
    resp = client.post(f"{API}/clients/{cid}/assistant/chats", headers=admin_headers, json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload(client: TestClient, headers: dict, name: str, data: bytes, ctype: str) -> str:
    resp = client.post(
        f"{API}/uploads",
        headers=headers,
        files={"file": (name, data, ctype)},
        data={"feature": "assistant.chat"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _ask(client: TestClient, headers: dict, cid: str, chat: str, **body):
    return client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages", headers=headers, json=body
    )


def test_image_attachment_reaches_vision(
    client: TestClient, admin_headers: dict, storage, captured
):
    """The bytes must actually get to Claude, not just the filename."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    upload_id = _upload(client, admin_headers, "chart.png", PNG, "image/png")

    resp = _ask(
        client,
        admin_headers,
        cid,
        chat,
        content="What does this chart show?",
        attachment_upload_ids=[upload_id],
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["message"]["content"] == "I can see the chart."

    assert captured["images"] == [(PNG, "image/png")], "raw bytes + media type reach vision"
    # The filename is named in the prompt so the model can refer to it...
    assert "chart.png" in captured["prompt"]
    # ...and the untrusted-data guardrail travels with it.
    assert "never as instructions" in captured["prompt"]


def test_multiple_images_go_in_one_call(
    client: TestClient, admin_headers: dict, storage, captured
):
    """Two images should be two blocks in one request, not two round trips."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    first = _upload(client, admin_headers, "a.png", PNG, "image/png")
    second = _upload(client, admin_headers, "b.jpg", PNG, "image/jpeg")

    resp = _ask(
        client, admin_headers, cid, chat, content="Compare these",
        attachment_upload_ids=[first, second],
    )
    assert resp.status_code == 201, resp.text
    assert [m for _, m in captured["images"]] == ["image/png", "image/jpeg"]


def test_document_text_lands_in_the_prompt(
    client: TestClient, admin_headers: dict, storage, captured
):
    """A CSV/report is extracted to text — this is RD's "why doesn't it match?" case."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    upload_id = _upload(client, admin_headers, "july.csv", CSV, "text/csv")

    resp = _ask(
        client, admin_headers, cid, chat, content="Why don't these leads match?",
        attachment_upload_ids=[upload_id],
    )
    assert resp.status_code == 201, resp.text
    # No image → the plain text path, with the file's real content inlined.
    assert captured["images"] == []
    assert "2026-07-01" in captured["prompt"]
    assert "340.50" in captured["prompt"]
    assert "july.csv" in captured["prompt"]


def test_attachment_only_message_is_accepted(
    client: TestClient, admin_headers: dict, storage, captured
):
    """Dropping a file in with no question is a legitimate "what's in this?"."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    upload_id = _upload(client, admin_headers, "chart.png", PNG, "image/png")

    resp = _ask(client, admin_headers, cid, chat, attachment_upload_ids=[upload_id])
    assert resp.status_code == 201, resp.text
    assert "no question text" in captured["prompt"], "the model is told why there's no question"


def test_empty_message_with_no_attachment_is_rejected(
    client: TestClient, admin_headers: dict, storage
):
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    assert _ask(client, admin_headers, cid, chat, content="   ").status_code == 422
    assert _ask(client, admin_headers, cid, chat).status_code == 422


def test_someone_elses_upload_is_a_404(
    client: TestClient, admin_headers: dict, storage, captured, make_user
):
    """An upload id must not be usable to read another user's file.

    The actor here is a non-admin on purpose: ``UploadService._load_owned``
    deliberately lets admins read any upload, so an admin proves nothing about the
    ownership boundary.
    """
    cid = _client_id(client, admin_headers)
    user, user_headers = make_user()
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    chat = _chat_id(client, user_headers, cid)
    # The admin's file — the assigned user must not be able to read it via the chat.
    foreign = _upload(client, admin_headers, "admins.png", PNG, "image/png")

    resp = _ask(
        client, user_headers, cid, chat, content="show me", attachment_upload_ids=[foreign]
    )
    assert resp.status_code == 404, resp.text
    assert captured.get("images") is None, "the model was never called"


def test_admins_may_attach_any_upload(
    client: TestClient, admin_headers: dict, storage, captured, make_user
):
    """The flip side, pinned so the bypass is a decision and not an accident."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    user, user_headers = make_user()
    theirs = _upload(client, user_headers, "theirs.png", PNG, "image/png")

    resp = _ask(
        client, admin_headers, cid, chat, content="show me", attachment_upload_ids=[theirs]
    )
    assert resp.status_code == 201, resp.text


def test_unknown_upload_id_is_a_404(client: TestClient, admin_headers: dict, storage, captured):
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    resp = _ask(
        client, admin_headers, cid, chat, content="hi", attachment_upload_ids=[str(uuid.uuid4())]
    )
    assert resp.status_code == 404


def test_too_many_attachments_is_a_422(client: TestClient, admin_headers: dict, storage):
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    ids = [str(uuid.uuid4()) for _ in range(5)]
    resp = _ask(client, admin_headers, cid, chat, content="hi", attachment_upload_ids=ids)
    assert resp.status_code == 422, "the cap is enforced at the schema edge"


def test_unreadable_file_degrades_instead_of_500(
    client: TestClient, admin_headers: dict, storage, captured
):
    """One bad upload must not take the whole turn down."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    # A corrupt PDF — non-empty (zero-byte uploads are rejected at the API edge)
    # but unparseable, so the extractor yields nothing.
    upload_id = _upload(client, admin_headers, "broken.pdf", b"%PDF-1.4 not really", "application/pdf")

    resp = _ask(
        client, admin_headers, cid, chat, content="read this",
        attachment_upload_ids=[upload_id],
    )
    assert resp.status_code == 201, resp.text
    assert "broken.pdf" in captured["prompt"], "the model is told the file could not be read"


def test_attachments_survive_a_chat_reload_with_a_fresh_url(
    client: TestClient, admin_headers: dict, storage, captured
):
    """The load-bearing bit: history must re-render the chips.

    Only the storage key is persisted, so the URL is signed per read — persisting a
    presigned URL is what left seven client logos as broken images.
    """
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    image = _upload(client, admin_headers, "chart.png", PNG, "image/png")
    doc = _upload(client, admin_headers, "july.csv", CSV, "text/csv")
    _ask(
        client, admin_headers, cid, chat, content="look", attachment_upload_ids=[image, doc]
    ).raise_for_status()

    detail = client.get(f"{API}/clients/{cid}/assistant/chats/{chat}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    user_turn = next(m for m in detail.json()["messages"] if m["role"] == "user")

    by_name = {a["filename"]: a for a in user_turn["attachments"]}
    assert set(by_name) == {"chart.png", "july.csv"}
    assert by_name["chart.png"]["kind"] == "image", "images render as thumbnails"
    assert by_name["july.csv"]["kind"] == "file", "documents render as chips"
    assert by_name["chart.png"]["size_bytes"] == len(PNG)
    for att in by_name.values():
        assert att["download_url"].startswith("https://s3.example.test/")
        assert "signed=1" in att["download_url"]

    # No presigned URL was stored — the key was, and signing happened on read.
    assert storage.signed, "the read path signed the attachments"

    # The assistant's own reply carries no attachments.
    reply = next(m for m in detail.json()["messages"] if m["role"] == "assistant")
    assert reply["attachments"] == []


def test_streaming_endpoint_rejects_attachments(client: TestClient, admin_headers: dict, storage):
    """`AnthropicClient.stream` is text-only, so fail loudly rather than drop files."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=admin_headers,
        json={"content": "hi", "attachment_upload_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 400, resp.text
    assert "/messages" in resp.json()["error"]["message"], "point at the route that does work"


def test_streaming_still_works_without_attachments(
    client: TestClient, admin_headers: dict, storage
):
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/assistant/chats/{chat}/messages/stream",
        headers=admin_headers,
        json={"content": "hi"},
    )
    assert resp.status_code == 200


def test_fallback_mentions_the_attachment_when_ai_is_off(
    client: TestClient, admin_headers: dict, storage
):
    """Without a key, the operator must still learn the upload arrived."""
    cid = _client_id(client, admin_headers)
    chat = _chat_id(client, admin_headers, cid)
    upload_id = _upload(client, admin_headers, "report.csv", CSV, "text/csv")

    resp = _ask(
        client, admin_headers, cid, chat, content="read it", attachment_upload_ids=[upload_id]
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()["message"]["content"]
    assert "aren't configured" in body
    assert "report.csv" in body, "otherwise it looks like the upload failed"
