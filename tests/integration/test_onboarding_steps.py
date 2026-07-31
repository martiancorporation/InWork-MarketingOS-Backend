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
    assert body["client"]["status"] == "draft"
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
        json={"step": 3, "platforms": ["google-search-console"]},
    )
    channels = {p["channel"] for p in resp.json()["client"]["platforms"]}
    # replaced, and deduped/lowercased earlier
    assert channels == {"google-search-console"}


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
    # finishing the wizard flips a draft to active
    assert body["client"]["status"] == "active"


def test_patch_unknown_client_404(client: TestClient, admin_headers: dict):
    resp = client.patch(
        f"{API}/clients/00000000-0000-0000-0000-000000000000/onboarding",
        headers=admin_headers,
        json={"step": 2},
    )
    assert resp.status_code == 404


def test_progressive_endpoints_require_auth(client: TestClient):
    assert client.post(f"{API}/clients/onboarding/draft", json=DRAFT).status_code == 401


# --------------------------------------------------------------------------- #
# Parity fixes: the wizard and the API had drifted apart, in ways that lost data.
# --------------------------------------------------------------------------- #


def _draft_id(client: TestClient, admin_headers: dict, **overrides) -> str:
    payload = {**DRAFT, **overrides}
    resp = client.post(f"{API}/clients/onboarding/draft", headers=admin_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def test_compliance_feed_is_readable_so_resume_can_repopulate_it(
    client: TestClient, admin_headers: dict
):
    """The bug this guards: ``GET /clients/{id}`` did not return the compliance
    note, so the wizard reopened that step blank and saved an empty feed over
    the real rules. The text must survive a resume round-trip."""
    cid = _draft_id(client, admin_headers)
    rules = "Never say 'cheap' or 'guaranteed'. Always include 'Made in USA'."
    saved = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 5, "compliance": {"feed": rules}},
    )
    assert saved.status_code == 200, saved.text

    # What the wizard reloads on resume must contain the rules.
    fetched = client.get(f"{API}/clients/{cid}", headers=admin_headers)
    assert fetched.status_code == 200
    assert fetched.json()["compliance_feed"] == rules

    # And re-sending what it read back must not destroy it.
    again = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 5, "compliance": {"feed": fetched.json()["compliance_feed"]}},
    )
    assert again.status_code == 200
    assert (
        client.get(f"{API}/clients/{cid}", headers=admin_headers).json()["compliance_feed"] == rules
    )


def test_blank_compliance_feed_still_clears_it_deliberately(
    client: TestClient, admin_headers: dict
):
    """Clearing must remain possible — the fix is that the UI no longer sends a
    blank feed by accident, not that a blank feed is ignored."""
    cid = _draft_id(client, admin_headers)
    client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 5, "compliance": {"feed": "Some rule."}},
    )
    client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 5, "compliance": {"feed": ""}},
    )
    assert (
        client.get(f"{API}/clients/{cid}", headers=admin_headers).json()["compliance_feed"] is None
    )


def test_measurement_channels_are_accepted(client: TestClient, admin_headers: dict):
    """The wizard offered Google Analytics and Search Console but the API rejected
    them, so the frontend filtered them out and the selection vanished silently."""
    cid = _draft_id(client, admin_headers)
    resp = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={
            "step": 3,
            "platforms": [
                "meta",
                "google-ads",
                "google-lsa",
                "google-analytics",
                "google-search-console",
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    channels = {p["channel"] for p in resp.json()["client"]["platforms"]}
    assert channels == {
        "meta",
        "google-ads",
        "google-lsa",
        "google-analytics",
        "google-search-console",
    }


def test_channels_not_offered_by_the_wizard_are_rejected(client: TestClient, admin_headers: dict):
    """The allow-list must mirror the wizard's picker exactly — anything the UI
    cannot select is not a valid channel, so a stray value fails loudly."""
    cid = _draft_id(client, admin_headers)
    for channel in ("seo", "influencer", "tiktok", "linkedin", "email"):
        resp = client.patch(
            f"{API}/clients/{cid}/onboarding",
            headers=admin_headers,
            json={"step": 3, "platforms": [channel]},
        )
        assert resp.status_code == 422, f"{channel} should be rejected: {resp.text}"


def test_timezone_round_trips_through_the_draft(client: TestClient, admin_headers: dict):
    """The US-reporting requirement: reporting is bucketed by the client's own
    local day, so the zone has to be captured and stored."""
    cid = _draft_id(client, admin_headers, timezone="America/New_York")
    assert client.get(f"{API}/clients/{cid}", headers=admin_headers).json()["timezone"] == (
        "America/New_York"
    )

    moved = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 1, "basics": {"timezone": "America/Los_Angeles"}},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["client"]["timezone"] == "America/Los_Angeles"


def test_invalid_timezone_is_rejected(client: TestClient, admin_headers: dict):
    bad = client.post(
        f"{API}/clients/onboarding/draft",
        headers=admin_headers,
        json={**DRAFT, "timezone": "EST5EDT-ish"},
    )
    assert bad.status_code == 422, bad.text

    cid = _draft_id(client, admin_headers)
    step = client.patch(
        f"{API}/clients/{cid}/onboarding",
        headers=admin_headers,
        json={"step": 1, "basics": {"timezone": "Mars/Olympus_Mons"}},
    )
    assert step.status_code == 422


def test_blank_timezone_is_treated_as_unset(client: TestClient, admin_headers: dict):
    cid = _draft_id(client, admin_headers, timezone="   ")
    assert client.get(f"{API}/clients/{cid}", headers=admin_headers).json()["timezone"] is None
