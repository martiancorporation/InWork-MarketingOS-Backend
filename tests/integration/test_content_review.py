"""API tests: pre-publish content review (SEO + deterministic compliance + AI brand voice)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _add_rule(client, admin_headers, cid, kind, text):
    resp = client.post(
        f"{API}/clients/{cid}/compliance",
        headers=admin_headers,
        json={"kind": kind, "text": text},
    )
    assert resp.status_code == 201, resp.text


def test_review_good_content(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/content/review",
        headers=admin_headers,
        json={
            "content": "Book your free roof inspection today! Trusted local experts. #roofing #tampa",
            "platform": "instagram",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ai_generated"] is False  # no Claude key in tests
    assert 0 <= body["seo"]["score"] <= 100
    assert body["compliance"]["passed"] is True
    assert body["brand_voice_aligned"] is None  # AI judge didn't run


def test_review_flags_banned_and_missing_required(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _add_rule(client, admin_headers, cid, "banned", "guaranteed")
    _add_rule(client, admin_headers, cid, "required", "Licensed & insured")

    resp = client.post(
        f"{API}/clients/{cid}/content/review",
        headers=admin_headers,
        json={"content": "Guaranteed results! Call us now to book. #deal", "platform": "facebook"},
    )
    assert resp.status_code == 200, resp.text
    comp = resp.json()["compliance"]
    assert comp["passed"] is False
    assert "guaranteed" in comp["violations"]
    assert "Licensed & insured" in comp["missing_required"]


def test_review_seo_flags_short_no_cta_no_hashtags(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/content/review",
        headers=admin_headers,
        json={"content": "Nice roof.", "platform": "instagram"},
    )
    assert resp.status_code == 200, resp.text
    seo = resp.json()["seo"]
    assert seo["score"] < 100
    assert any("short" in f.lower() for f in seo["findings"])
    assert any("hashtag" in f.lower() for f in seo["findings"])


def test_review_uses_ai_when_configured(client: TestClient, admin_headers: dict, monkeypatch):
    from app.integrations.anthropic.client import AnthropicClient

    async def fake_complete(self, *, system, prompt, max_tokens=None, context=None):
        return '{"brand_voice_aligned": false, "issues": ["Tone is too casual"], "suggestions": ["Match the confident brand voice"]}'

    monkeypatch.setattr(AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(AnthropicClient, "complete", fake_complete)

    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/content/review",
        headers=admin_headers,
        json={"content": "yo check out our roofs lol", "platform": "instagram"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ai_generated"] is True
    assert body["brand_voice_aligned"] is False
    assert "Tone is too casual" in body["issues"]


def test_review_requires_content(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/content/review", headers=admin_headers, json={"content": ""}
    )
    assert resp.status_code == 422


def test_review_unassigned_user_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/content/review",
        headers=user_headers,
        json={"content": "hello there friends"},
    )
    assert resp.status_code == 404
