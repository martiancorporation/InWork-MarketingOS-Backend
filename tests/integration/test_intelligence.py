"""Integration tests for the client intelligence pipeline + API.

Runs the build hermetically: the orchestrator is driven directly on the test
session with the deterministic local embedder and the agents' deterministic
fallback (no Claude key, no network). Endpoints are exercised via the API.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.integrations.embeddings.fake import FakeEmbedder
from app.models.client import Client
from app.services.intelligence.orchestrator import IntelligenceOrchestrator
from tests.helpers import onboarding_payload

API = "/api/v1"

NO_AI_FEED = "We do not want AI-generated text. Never say 'cheap'. Always include 'Made in USA'."


def _onboard(client: TestClient, admin_headers, **compliance) -> str:
    payload = onboarding_payload(compliance={"feed": compliance.get("feed", NO_AI_FEED)})
    resp = client.post(f"{API}/clients/onboarding", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _build(db_session: Session, client_id: str, job_type: str = "full_build"):
    client = db_session.get(Client, uuid.UUID(client_id))
    orch = IntelligenceOrchestrator(db_session, embedder=FakeEmbedder(1024), storage=None)
    return asyncio.run(orch.build(client, job_type=job_type))


# ---- async status ----

def test_onboarding_returns_building_status(client: TestClient, admin_headers) -> None:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload()
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["intelligence"]["status"] == "building"


# ---- full build ----

def test_full_build_creates_profile_directives_and_flags(
    client: TestClient, admin_headers, db_session: Session
) -> None:
    cid = _onboard(client, admin_headers)
    profile = _build(db_session, cid)
    assert profile.version == 1

    intel = client.get(f"{API}/clients/{cid}/intelligence", headers=admin_headers).json()
    assert intel["status"] == "ready"
    assert intel["version"] == 1
    assert intel["profile"]["summary_md"]
    # The "no AI-generated text" rule became a deterministic capability flag...
    assert intel["profile"]["capability_flags"].get("ai_text_generation") is False
    # ...and a mandatory directive.
    assert any(d["tier"] == "mandatory" for d in intel["directives"])

    status = client.get(f"{API}/clients/{cid}/intelligence/status", headers=admin_headers)
    assert status.json()["status"] == "ready"
    assert status.json()["version"] == 1


def test_context_endpoint_exposes_rules_and_retrieval(
    client: TestClient, admin_headers, db_session: Session
) -> None:
    cid = _onboard(client, admin_headers)
    _build(db_session, cid)
    ctx = client.get(
        f"{API}/clients/{cid}/context",
        headers=admin_headers,
        params={"query": "what is the brand voice and tone"},
    ).json()
    assert "HARD RULES" in ctx["preamble"]
    assert ctx["capability_flags"].get("ai_text_generation") is False
    assert len(ctx["retrieved"]) > 0  # RAG chunks were embedded + retrieved


# ---- incremental / versioning ----

def test_incremental_build_bumps_version_and_supersedes(
    client: TestClient, admin_headers, db_session: Session
) -> None:
    cid = _onboard(client, admin_headers)
    _build(db_session, cid)  # v1
    # Change an onboarding field, then rebuild incrementally.
    client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"goals": "New goal: dominate the EU developer market in 2027.", "step": 4},
    )
    profile2 = _build(db_session, cid, job_type="incremental")
    assert profile2.version == 2

    versions = client.get(
        f"{API}/clients/{cid}/intelligence/versions", headers=admin_headers
    ).json()
    assert [v["version"] for v in versions] == [2, 1]
    v1 = next(v for v in versions if v["version"] == 1)
    assert v1["status"] == "superseded"
    # Current pointer is v2.
    assert client.get(
        f"{API}/clients/{cid}/intelligence/status", headers=admin_headers
    ).json()["version"] == 2


# ---- admin rebuild + conflict resolution ----

def test_rebuild_enqueues_job(client: TestClient, admin_headers, db_session: Session) -> None:
    cid = _onboard(client, admin_headers)
    _build(db_session, cid)
    resp = client.post(f"{API}/clients/{cid}/intelligence/rebuild", headers=admin_headers)
    assert resp.status_code == 200
    # The current profile stays "ready" (served with no downtime) while the
    # queued rebuild is signalled separately via job_status.
    assert resp.json()["status"] == "ready"
    assert resp.json()["job_status"] == "queued"


def test_resolve_directive_dismiss(
    client: TestClient, admin_headers, db_session: Session
) -> None:
    cid = _onboard(client, admin_headers)
    _build(db_session, cid)
    directives = client.get(
        f"{API}/clients/{cid}/intelligence", headers=admin_headers
    ).json()["directives"]
    did = directives[0]["id"]
    resp = client.post(
        f"{API}/clients/{cid}/directives/{did}/resolve",
        headers=admin_headers,
        params={"activate": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "superseded"


# ---- access scoping ----

def test_intelligence_requires_access(client: TestClient, admin_headers, make_user, db_session) -> None:
    cid = _onboard(client, admin_headers)
    _build(db_session, cid)
    _, outsider = make_user(email="outsider@test.com", password="passwordX1")
    # Unassigned non-admin cannot see the client → 404 (not 403).
    assert client.get(f"{API}/clients/{cid}/intelligence", headers=outsider).status_code == 404


def test_intelligence_unauthenticated(client: TestClient) -> None:
    assert client.get(f"{API}/clients/{uuid.uuid4()}/intelligence").status_code == 401
