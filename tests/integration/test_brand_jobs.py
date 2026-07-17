"""API tests: async brand-extraction jobs (transaction-id + poll)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient

from app.services.brand_job_service import BrandJobService
from tests.conftest import API


def test_create_job_returns_transaction_id_pending(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/clients/onboarding/extract-brand/jobs",
        headers=admin_headers,
        json={"website": "acme.com"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["id"]  # the transaction id to poll
    assert body["status"] == "pending"
    assert body["result"] is None


def test_poll_job(client: TestClient, admin_headers: dict):
    created = client.post(
        f"{API}/clients/onboarding/extract-brand/jobs",
        headers=admin_headers,
        json={"website": "acme.com"},
    ).json()
    got = client.get(
        f"{API}/clients/onboarding/extract-brand/jobs/{created['id']}", headers=admin_headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["id"] == created["id"]


def test_process_completes_job(client: TestClient, admin_headers: dict, db_session, monkeypatch):
    # Avoid a real network fetch: no browser, no scrape → deterministic fallback.
    async def no_render(url, **kw):
        return None

    monkeypatch.setattr("app.ai.brand_extraction.render_page", no_render)
    monkeypatch.setattr("app.ai.brand_extraction.fetch_page", lambda url, **kw: None)

    created = client.post(
        f"{API}/clients/onboarding/extract-brand/jobs",
        headers=admin_headers,
        json={"website": "https://unreachable.example"},
    ).json()

    # Drive the background processing against the test session, then poll.
    asyncio.run(BrandJobService.process(uuid.UUID(created["id"]), session=db_session))

    got = client.get(
        f"{API}/clients/onboarding/extract-brand/jobs/{created['id']}", headers=admin_headers
    ).json()
    assert got["status"] == "done", got
    assert got["result"] is not None
    assert got["result"]["ai_generated"] is False  # deterministic fallback


def test_job_owner_scoped_404(client: TestClient, admin_headers: dict, make_user):
    created = client.post(
        f"{API}/clients/onboarding/extract-brand/jobs",
        headers=admin_headers,
        json={"website": "acme.com"},
    ).json()
    _user, user_headers = make_user()
    # A non-admin who doesn't own the job can't see it.
    resp = client.get(
        f"{API}/clients/onboarding/extract-brand/jobs/{created['id']}", headers=user_headers
    )
    assert resp.status_code == 404


def test_create_job_requires_a_source(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/clients/onboarding/extract-brand/jobs", headers=admin_headers, json={}
    )
    assert resp.status_code == 422
