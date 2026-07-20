"""API tests: onboarding cross-field consistency check (review-step guardrail).

Runs against the deterministic fallback (the hermetic suite has no Anthropic
key), so findings are the rule-based ones mirroring the web check."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, **overrides):
    payload = onboarding_payload(**overrides)
    resp = client.post(f"{API}/clients/onboarding", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def test_default_client_returns_findings(client: TestClient, admin_headers: dict):
    # Platforms selected, but onboarding connects no integrations → a warn.
    cid = _client_id(client, admin_headers)
    resp = client.post(f"{API}/clients/{cid}/onboarding/consistency", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ai_generated"] is False  # fallback path (no API key in tests)
    assert data["has_blocking"] is False
    assert any(
        f["level"] == "warn" and "integrations" in f["message"].lower() for f in data["findings"]
    )


def test_banned_word_is_a_blocking_error(client: TestClient, admin_headers: dict):
    cid = _client_id(
        client,
        admin_headers,
        brand={"brand_voice": "We are the guaranteed best in town."},
    )
    # Add a banned-word compliance rule that the brand voice violates.
    assert (
        client.post(
            f"{API}/clients/{cid}/compliance",
            headers=admin_headers,
            json={"kind": "banned", "text": "guaranteed"},
        ).status_code
        == 201
    )
    resp = client.post(f"{API}/clients/{cid}/onboarding/consistency", headers=admin_headers)
    data = resp.json()
    assert data["has_blocking"] is True
    assert any(f["level"] == "error" and "guaranteed" in f["message"] for f in data["findings"])


def test_consistency_is_admin_only(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    _user, headers = make_user(email="user@test.com")
    resp = client.post(f"{API}/clients/{cid}/onboarding/consistency", headers=headers)
    assert resp.status_code == 403


def test_consistency_requires_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.post(f"{API}/clients/{cid}/onboarding/consistency").status_code == 401
