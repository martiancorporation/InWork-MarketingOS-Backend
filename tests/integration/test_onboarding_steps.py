"""API tests: the progressive (step-by-step) onboarding flow.

Draft gate → partial per-step autosave → document attach → finalize. Each call
returns the recomputed readiness score and wizard progress.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API

DRAFT = {
    "name": "Acme Co.",
    "business_type": "DTC E-commerce",
    "industry": "Home & Garden",
    "website": "https://acme.com",
    "markets": "Entire US",
}


def _draft(client, headers, **overrides):
    body = {**DRAFT, **overrides}
    return client.post(f"{API}/clients/onboarding/draft", headers=headers, json=body)


def test_draft_opens_onboarding_client_at_step_1(client: TestClient, admin_headers: dict):
    resp = _draft(client, admin_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["client"]["slug"] == "acme-co"
    assert body["client"]["status"] == "onboarding"
    assert body["client"]["onboarding_step"] == 1
    assert body["onboarding"] == {
        "step": 1,
        "total_steps": 8,
        "percent": 13,
        "completed": False,
    }
    assert 0 <= body["readiness"]["score"] <= 100
    # brand voice hasn't been provided yet
    assert any(m["key"] == "brand-voice" for m in body["readiness"]["missing"])


def test_draft_requires_mandatory_basics(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/clients/onboarding/draft",
        headers=admin_headers,
        json={"name": "No Industry", "business_type": "DTC"},  # industry missing
    )
    assert resp.status_code == 422


def test_non_admin_cannot_start_draft(client: TestClient, make_user):
    _, user_headers = make_user()
    assert _draft(client, user_headers).status_code == 403


def test_patch_step_saves_brand_and_advances_progress(client: TestClient, admin_headers: dict):
    cid = _draft(client, admin_headers).json()["client"]["id"]
    resp = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={
            "step": 2,
            "brand": {
                "brand_voice": "Friendly, witty, never corporate.",
                "about_brand": "Joyful home goods.",
                "colors": [{"hex": "#0EA5E9"}, {"hex": "#1E3A8A"}],
                "fonts": ["Inter"],
                "logo_url": "https://acme.com/logo.svg",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarding"]["step"] == 2
    assert body["onboarding"]["percent"] == 25
    assert body["client"]["brand_voice"].startswith("Friendly")
    assert len(body["client"]["brand_colors"]) == 2
    # readiness picked up brand-voice / about / colors / logo
    completed = set(body["readiness"]["completed"])
    assert "Brand voice defined" in completed
    assert "Brand colors added" in completed


def test_patch_is_partial_and_does_not_clobber_prior_steps(client: TestClient, admin_headers: dict):
    cid = _draft(client, admin_headers).json()["client"]["id"]
    client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 2, "brand": {"brand_voice": "Bold and clear."}},
    )
    # A later step that only sends goals must leave brand_voice intact.
    resp = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 4, "goals": "Q1 brand, Q2 lead-gen, Q3 ecommerce push."},
    )
    body = resp.json()
    assert body["client"]["brand_voice"] == "Bold and clear."
    assert body["client"]["goals"].startswith("Q1")
    assert body["onboarding"]["step"] == 4


def test_patch_replaces_platforms(client: TestClient, admin_headers: dict):
    cid = _draft(client, admin_headers).json()["client"]["id"]
    client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 3, "platforms": ["meta", "Meta", "google-ads"]},
    )
    resp = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 3, "platforms": ["seo"]},
    )
    channels = {p["channel"] for p in resp.json()["client"]["platforms"]}
    assert channels == {"seo"}  # replaced, and deduped/lowercased earlier


def test_step_advances_monotonically(client: TestClient, admin_headers: dict):
    cid = _draft(client, admin_headers).json()["client"]["id"]
    client.patch(f"{API}/clients/{cid}/onboarding", headers=admin_headers, json={"step": 5})
    # a lower step number must not roll progress back
    resp = client.patch(f"{API}/clients/{cid}/onboarding", headers=admin_headers, json={"step": 3})
    assert resp.json()["onboarding"]["step"] == 5


def test_attach_documents(client: TestClient, admin_headers: dict):
    cid = _draft(client, admin_headers).json()["client"]["id"]
    resp = client.post(
        f"{API}/clients/{cid}/documents",
        headers=admin_headers,
        json={
            "documents": [
                {
                    "name": "brand-book.pdf",
                    "kind": "brand",
                    "size_bytes": 1024,
                    "mime_type": "application/pdf",
                    "storage_url": "s3://bucket/brand-book.pdf",
                }
            ]
        },
    )
    assert resp.status_code == 201


def test_complete_finalizes_at_100_percent(client: TestClient, admin_headers: dict):
    cid = _draft(client, admin_headers).json()["client"]["id"]
    resp = client.post(f"{API}/clients/{cid}/onboarding/complete", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarding"] == {
        "step": 8,
        "total_steps": 8,
        "percent": 100,
        "completed": True,
    }
    assert body["client"]["onboarding_step"] == 8


def test_patch_unknown_client_404(client: TestClient, admin_headers: dict):
    resp = client.patch(
        f"{API}/clients/00000000-0000-0000-0000-000000000000/onboarding",
        headers=admin_headers,
        json={"step": 2},
    )
    assert resp.status_code == 404


def test_progressive_endpoints_require_auth(client: TestClient):
    assert client.post(f"{API}/clients/onboarding/draft", json=DRAFT).status_code == 401
