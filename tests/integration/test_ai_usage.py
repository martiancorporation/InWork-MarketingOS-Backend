"""API tests for the AI usage analytics endpoints.

Recording is disabled in the test env (see conftest), so these seed
``ai_usage_events`` rows directly and assert the read/aggregation endpoints,
their filters, and access control.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.ai_usage import AiUsageEvent
from app.models.client import Client
from tests.conftest import API


def _client_row(db: Session) -> Client:
    c = Client(slug=f"seed-{uuid.uuid4().hex[:8]}", name="Seed Co")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _event(db: Session, **kw):
    defaults = dict(
        feature="onboarding.brand_extraction",
        provider="anthropic",
        model="claude-opus-4-8",
        operation="complete",
        input_tokens=1000,
        output_tokens=500,
        cache_write_tokens=0,
        cache_read_tokens=0,
        total_tokens=1500,
        input_cost=0.015,
        output_cost=0.0375,
        cache_cost=0,
        total_cost=0.0525,
        currency="USD",
        priced=True,
        status="success",
    )
    defaults.update(kw)
    row = AiUsageEvent(**defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed(db: Session):
    c = _client_row(db)
    _event(db, client_id=c.id, feature="onboarding.brand_extraction", total_cost=0.05, total_tokens=1500)
    _event(db, client_id=c.id, feature="project_ai.chat", model="claude-sonnet-5", total_cost=0.01, total_tokens=800)
    _event(db, client_id=None, feature="assistant.global", total_cost=0.02, total_tokens=300)
    return c


def test_detailed_log_lists_events(client: TestClient, admin_headers: dict, db_session: Session):
    _seed(db_session)
    resp = client.get(f"{API}/ai-usage", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert {"items", "total", "page", "page_size"} <= set(body)
    # each item carries tokens + cost + attribution
    row = body["items"][0]
    assert {"feature", "model", "total_tokens", "total_cost", "actor_user_id", "client_id"} <= set(row)


def test_detailed_log_filters_by_feature(client: TestClient, admin_headers: dict, db_session: Session):
    _seed(db_session)
    resp = client.get(f"{API}/ai-usage?feature=project_ai.chat", headers=admin_headers)
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["feature"] == "project_ai.chat"


def test_platform_summary_totals_and_breakdowns(client: TestClient, admin_headers: dict, db_session: Session):
    _seed(db_session)
    resp = client.get(f"{API}/ai-usage/summary", headers=admin_headers)
    assert resp.status_code == 200
    s = resp.json()
    assert s["totals"]["requests"] == 3
    assert s["totals"]["total_tokens"] == 2600  # 1500 + 800 + 300
    assert abs(s["totals"]["total_cost"] - 0.08) < 1e-6  # 0.05 + 0.01 + 0.02
    features = {r["key"] for r in s["by_feature"]}
    assert {"onboarding.brand_extraction", "project_ai.chat", "assistant.global"} == features
    models = {r["key"] for r in s["by_model"]}
    assert {"claude-opus-4-8", "claude-sonnet-5"} == models
    assert len(s["daily"]) >= 1


def test_client_summary_scoped_for_admin(client: TestClient, admin_headers: dict, db_session: Session):
    c = _seed(db_session)
    resp = client.get(f"{API}/ai-usage/clients/{c.id}/summary", headers=admin_headers)
    assert resp.status_code == 200
    s = resp.json()
    assert s["client_id"] == str(c.id)
    assert s["totals"]["requests"] == 2  # only the two client-attributed events
    assert abs(s["totals"]["total_cost"] - 0.06) < 1e-6


def test_client_summary_unknown_client_404(client: TestClient, admin_headers: dict):
    resp = client.get(f"{API}/ai-usage/clients/{uuid.uuid4()}/summary", headers=admin_headers)
    assert resp.status_code == 404


def test_client_summary_hidden_from_unassigned_user(
    client: TestClient, admin_headers: dict, make_user, db_session: Session
):
    c = _seed(db_session)
    _, user_headers = make_user()
    # Non-admin isn't assigned to this client → 404 (existence hidden).
    resp = client.get(f"{API}/ai-usage/clients/{c.id}/summary", headers=user_headers)
    assert resp.status_code == 404


def test_detailed_log_requires_admin(client: TestClient, make_user, db_session: Session):
    _seed(db_session)
    _, user_headers = make_user()
    assert client.get(f"{API}/ai-usage", headers=user_headers).status_code == 403


def test_ai_usage_requires_auth(client: TestClient):
    assert client.get(f"{API}/ai-usage").status_code == 401
    assert client.get(f"{API}/ai-usage/summary").status_code == 401
