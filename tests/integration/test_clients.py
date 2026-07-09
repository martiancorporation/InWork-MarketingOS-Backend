"""API tests: client onboarding, listing, detail, and AI brand extraction."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _onboard(client, headers, **overrides):
    return client.post(f"{API}/clients/onboarding", headers=headers, json=onboarding_payload(**overrides))


def test_admin_onboards_client(client: TestClient, admin_headers: dict):
    resp = _onboard(client, admin_headers)
    assert resp.status_code == 201
    data = resp.json()
    c = data["client"]
    assert c["slug"] == "acme-co"
    assert c["status"] == "onboarding"
    assert c["pipeline_stage"] == "onboarding"
    assert len(c["brand_colors"]) == 2
    assert len(c["platforms"]) == 4
    assert {p["channel"] for p in c["platforms"]} == {"meta", "google-ads", "google-lsa", "seo"}
    assert len(c["contacts"]) == 2
    assert any(x["is_primary"] and x["side"] == "client" for x in c["contacts"])
    # readiness is computed and returned
    assert 0 <= data["readiness"]["score"] <= 100
    assert any(m["key"] == "integrations" for m in data["readiness"]["missing"])


def test_onboarding_generates_unique_slugs(client: TestClient, admin_headers: dict):
    first = _onboard(client, admin_headers)
    second = _onboard(client, admin_headers)  # same name again
    assert first.json()["client"]["slug"] == "acme-co"
    assert second.json()["client"]["slug"] == "acme-co-2"


def test_non_admin_cannot_onboard(client: TestClient, make_user):
    _, user_headers = make_user()
    assert _onboard(client, user_headers).status_code == 403


def test_onboarding_requires_at_least_one_platform(client: TestClient, admin_headers: dict):
    assert _onboard(client, admin_headers, platforms=[]).status_code == 422


def test_onboarding_requires_client_contact_email(client: TestClient, admin_headers: dict):
    assert _onboard(client, admin_headers, client_contacts=[{"name": "No Email"}]).status_code == 422


def test_onboarding_rejects_invalid_hex_color(client: TestClient, admin_headers: dict):
    bad_brand = {"brand_voice": "Voice", "colors": [{"hex": "blue"}]}
    assert _onboard(client, admin_headers, brand=bad_brand).status_code == 422


def test_onboarding_requires_brand_voice(client: TestClient, admin_headers: dict):
    assert _onboard(client, admin_headers, brand={"about_brand": "x"}).status_code == 422


def test_get_client_by_id(client: TestClient, admin_headers: dict):
    cid = _onboard(client, admin_headers).json()["client"]["id"]
    resp = client.get(f"{API}/clients/{cid}", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == cid


def test_get_unknown_client_404(client: TestClient, admin_headers: dict):
    resp = client.get(f"{API}/clients/00000000-0000-0000-0000-000000000000", headers=admin_headers)
    assert resp.status_code == 404


def test_list_pagination(client: TestClient, admin_headers: dict):
    _onboard(client, admin_headers, name="Acme One")
    _onboard(client, admin_headers, name="Acme Two")
    resp = client.get(f"{API}/clients?page=1&page_size=1", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["page_size"] == 1


def test_list_search_and_status_filter(client: TestClient, admin_headers: dict):
    _onboard(client, admin_headers, name="Acme Co", industry="Home & Garden")
    _onboard(client, admin_headers, name="Northwind", industry="DevTools")
    assert client.get(f"{API}/clients?search=north", headers=admin_headers).json()["total"] == 1
    assert client.get(f"{API}/clients?search=zzz", headers=admin_headers).json()["total"] == 0
    # freshly onboarded clients are status=onboarding, so filtering active -> 0
    assert client.get(f"{API}/clients?status=active", headers=admin_headers).json()["total"] == 0


def test_brand_extraction_fallback_without_api_key(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "https://acme.com"},
    )
    assert resp.status_code == 200
    # No ANTHROPIC_API_KEY in tests -> deterministic fallback.
    assert resp.json()["ai_generated"] is False


def test_brand_extraction_uses_ai_when_configured(client: TestClient, admin_headers: dict, monkeypatch):
    # Mock the Anthropic client so no real network call happens.
    from app.integrations.anthropic.client import AnthropicClient

    async def fake_complete(self, *, system, prompt, max_tokens=None):
        return '{"summary":"Warm DTC brand","colors":["#112233"],"fonts":["Inter"],"tone":"warm","imagery":"bright"}'

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "complete", fake_complete)

    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "https://acme.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_generated"] is True
    assert body["colors"] == ["#112233"]
    assert body["fonts"] == ["Inter"]


def test_brand_extraction_requires_a_source(client: TestClient, admin_headers: dict):
    resp = client.post(f"{API}/clients/onboarding/extract-brand", headers=admin_headers, json={})
    assert resp.status_code == 422


def test_clients_require_authentication(client: TestClient):
    assert client.get(f"{API}/clients").status_code == 401
