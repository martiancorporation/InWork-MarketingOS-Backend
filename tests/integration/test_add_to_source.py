"""API tests: conversations "add to source" — manual promotion of an email into
the client's knowledge/RAG layer (survives ingestion reconciliation)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeSource
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _thread(client, headers, cid):
    resp = client.post(
        f"{API}/clients/{cid}/conversations",
        headers=headers,
        json={"subject": "Change request", "body": "Please make the logo bigger.",
              "recipients": [{"email": "jane@acme.com"}]},
    )
    assert resp.status_code == 201, resp.text
    conv = resp.json()
    return conv["id"], conv["messages"][0]["id"]


def test_add_to_source_stamps_message_and_creates_source(
    client, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    conv_id, msg_id = _thread(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/conversations/{conv_id}/messages/{msg_id}/add-to-source",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added_to_source_at"] is not None
    assert body["knowledge_source_id"] is not None

    # A knowledge source row was created for the message.
    src = db_session.scalar(
        select(KnowledgeSource).where(KnowledgeSource.ref_kind == "message")
    )
    assert src is not None
    assert str(src.ref_id) == msg_id
    assert "logo bigger" in (src.extracted_text or "")


def test_add_to_source_is_idempotent(client, admin_headers: dict, db_session: Session):
    cid = _client_id(client, admin_headers)
    conv_id, msg_id = _thread(client, admin_headers, cid)
    url = f"{API}/clients/{cid}/conversations/{conv_id}/messages/{msg_id}/add-to-source"
    first = client.post(url, headers=admin_headers).json()
    second = client.post(url, headers=admin_headers).json()
    assert first["knowledge_source_id"] == second["knowledge_source_id"]
    count = len(
        db_session.scalars(
            select(KnowledgeSource).where(KnowledgeSource.ref_kind == "message")
        ).all()
    )
    assert count == 1  # not duplicated


def test_add_to_source_unknown_message_404(client, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    conv_id, _msg = _thread(client, admin_headers, cid)
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = client.post(
        f"{API}/clients/{cid}/conversations/{conv_id}/messages/{bogus}/add-to-source",
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_add_to_source_requires_auth(client, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    conv_id, msg_id = _thread(client, admin_headers, cid)
    resp = client.post(
        f"{API}/clients/{cid}/conversations/{conv_id}/messages/{msg_id}/add-to-source"
    )
    assert resp.status_code == 401
