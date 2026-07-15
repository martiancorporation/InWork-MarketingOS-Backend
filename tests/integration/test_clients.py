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


def _mock_render(monkeypatch, page):
    """Replace the headless render with a canned result (or None)."""

    async def fake_render_page(url, **kw):
        return page

    monkeypatch.setattr("app.ai.brand_extraction.render_page", fake_render_page)


def test_brand_extraction_fallback_without_api_key(client: TestClient, admin_headers: dict, monkeypatch):
    from app.utils.render import RenderedPage

    # Avoid a real browser render; still exercises the no-API-key path.
    _mock_render(
        monkeypatch,
        RenderedPage(text="site text", colors=["#0D6EFD"], fonts=["Fustat"], screenshot=b"jpg"),
    )
    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "https://acme.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # No ANTHROPIC_API_KEY in tests -> deterministic fallback, but measured
    # colors/fonts are still returned.
    assert body["ai_generated"] is False
    assert body["colors"] == ["#0D6EFD"]
    assert body["fonts"] == ["Fustat"]


def test_brand_extraction_render_plus_vision(client: TestClient, admin_headers: dict, monkeypatch):
    # The render supplies measured colors/fonts + a screenshot; one vision call
    # supplies the prose fields (plus anything the model observed). Merge keeps
    # measured values first.
    from app.integrations.anthropic.client import AnthropicClient
    from app.utils.render import RenderedPage

    async def fake_complete_with_image(self, *, system, prompt, image, media_type="image/jpeg", max_tokens=None, context=None):
        assert image == b"jpg"          # the screenshot reached the model
        assert "site text" in prompt    # so did the rendered text
        return '{"summary":"Warm DTC brand","colors":["#AABBCC"],"fonts":["Lora"],"tone":"warm","imagery":"bright"}'

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "complete_with_image", fake_complete_with_image)
    _mock_render(
        monkeypatch,
        RenderedPage(text="site text", colors=["#112233"], fonts=["Inter"], screenshot=b"jpg"),
    )

    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "https://acme.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_generated"] is True
    assert body["summary"] == "Warm DTC brand"
    assert body["tone"] == "warm"
    assert body["colors"] == ["#112233", "#AABBCC"]  # measured first, then model
    assert body["fonts"] == ["Inter", "Lora"]


def test_brand_extraction_scrape_fallback_when_render_unavailable(
    client: TestClient, admin_headers: dict, monkeypatch
):
    # No headless browser -> plain httpx scrape supplies text/colors/fonts and
    # the model call is text-only. The request still succeeds.
    from app.integrations.anthropic.client import AnthropicClient
    from app.utils.web import PageContent

    async def fake_complete(self, *, system, prompt, max_tokens=None, context=None):
        assert "site text" in prompt  # scraped text was passed as reference
        return '{"summary":"From scraped text","colors":[],"fonts":[],"tone":"plain","imagery":"minimal"}'

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "complete", fake_complete)
    _mock_render(monkeypatch, None)
    monkeypatch.setattr(
        "app.ai.brand_extraction.fetch_page",
        lambda url, **kw: PageContent(text="site text", colors=["#112233"], fonts=[]),
    )

    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "https://acme.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_generated"] is True
    assert body["summary"] == "From scraped text"
    assert body["colors"] == ["#112233"]


def test_brand_extraction_model_failure_degrades_to_measured_values(
    client: TestClient, admin_headers: dict, monkeypatch
):
    # Model call blows up -> deterministic response with measured colors/fonts.
    from app.integrations.anthropic.client import AnthropicClient
    from app.utils.render import RenderedPage

    async def broken_vision(self, *, system, prompt, image, media_type="image/jpeg", max_tokens=None):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "complete_with_image", broken_vision)
    _mock_render(
        monkeypatch,
        RenderedPage(text="site text", colors=["#112233"], fonts=["Inter"], screenshot=b"jpg"),
    )

    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "https://acme.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_generated"] is False
    assert body["colors"] == ["#112233"]
    assert body["fonts"] == ["Inter"]


def test_brand_extraction_prepends_declared_theme_color(
    client: TestClient, admin_headers: dict, monkeypatch
):
    # The site's declared theme-color leads the palette, ahead of measured colors.
    from app.utils.render import RenderedPage

    _mock_render(
        monkeypatch,
        RenderedPage(
            text="site text",
            colors=["#112233"],
            fonts=["Inter"],
            screenshot=b"jpg",
            theme_color="#0D6EFD",
        ),
    )
    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "acme.com"},  # bare domain accepted
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["colors"] == ["#0D6EFD", "#112233"]  # theme-color first


def test_brand_extraction_when_nothing_can_be_fetched(
    client: TestClient, admin_headers: dict, monkeypatch
):
    # Neither the browser nor the scrape returns anything: still a clean 200 with
    # a deterministic, clearly-provisional result (never a 500).
    _mock_render(monkeypatch, None)
    monkeypatch.setattr("app.ai.brand_extraction.fetch_page", lambda url, **kw: None)

    resp = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=admin_headers,
        json={"website": "https://unreachable.example"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_generated"] is False
    assert body["colors"] == [] and body["fonts"] == []
    assert "unreachable.example" in body["summary"]


def test_brand_extraction_requires_website(client: TestClient, admin_headers: dict):
    resp = client.post(f"{API}/clients/onboarding/extract-brand", headers=admin_headers, json={})
    assert resp.status_code == 422


def test_clients_require_authentication(client: TestClient):
    assert client.get(f"{API}/clients").status_code == 401
