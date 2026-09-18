"""API tests: website auto-fill for onboarding (``POST /clients/onboarding/auto-fill``).

Covers the router/service wiring end to end: admin-only, creates a draft when
no ``client_id`` is given vs. updates an existing one, ``onboarding_step``
never advances, and a hostname resolving to a private IP degrades to "nothing
found" through the exact same SSRF-hardened pipeline ``extract-brand`` uses
(see ``tests/unit/test_web.py``/``test_render.py`` for the guard's own unit
coverage — this proves the new endpoint actually goes through it).
"""

from __future__ import annotations

import socket

from fastapi.testclient import TestClient

from tests.conftest import API


def _mock_render(monkeypatch, page):
    async def fake_render_page(url, **kw):
        return page

    monkeypatch.setattr("app.ai.brand_extraction.render_page", fake_render_page)


def test_creates_a_draft_when_no_client_id_given(
    client: TestClient, admin_headers: dict, monkeypatch
):
    from app.utils.render import RenderedPage

    _mock_render(
        monkeypatch,
        RenderedPage(text="Tony's Garage — honest car care", colors=[], fonts=[], screenshot=b"x"),
    )
    resp = client.post(
        f"{API}/clients/onboarding/auto-fill",
        headers=admin_headers,
        json={"website": "https://tonysgarage.example"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client"]["website"] == "https://tonysgarage.example"
    # onboarding_step is never advanced by auto-fill.
    assert body["onboarding"]["step"] == 1
    assert body["onboarding"]["completed"] is False


def test_updates_an_existing_draft_without_overwriting_typed_fields(
    client: TestClient, admin_headers: dict, monkeypatch
):
    from app.utils.render import RenderedPage

    draft = client.post(
        f"{API}/clients/onboarding/draft",
        headers=admin_headers,
        json={
            "name": "Typed By Hand",
            "business_type": "Retail",
            "industry": "Already set",
            "website": "https://acme.example",
        },
    )
    assert draft.status_code == 201, draft.text
    client_id = draft.json()["client"]["id"]

    _mock_render(
        monkeypatch,
        RenderedPage(text="Acme site text", colors=["#0D6EFD"], fonts=["Inter"], screenshot=b"x"),
    )
    resp = client.post(
        f"{API}/clients/onboarding/auto-fill",
        headers=admin_headers,
        json={"website": "https://acme.example", "client_id": client_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client"]["id"] == client_id
    assert body["client"]["name"] == "Typed By Hand"  # untouched
    assert body["client"]["industry"] == "Already set"  # untouched
    assert body["onboarding"]["step"] == 1  # still untouched


def test_non_admin_cannot_auto_fill(client: TestClient, make_user):
    _, user_headers = make_user()
    resp = client.post(
        f"{API}/clients/onboarding/auto-fill",
        headers=user_headers,
        json={"website": "https://acme.example"},
    )
    assert resp.status_code == 403


def test_requires_a_website(client: TestClient, admin_headers: dict):
    resp = client.post(
        f"{API}/clients/onboarding/auto-fill", headers=admin_headers, json={}
    )
    assert resp.status_code == 422


def test_private_ip_host_degrades_to_nothing_found(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """A hostname that resolves to a private/link-local address must never be
    fetched — same guard proven at the unit level (test_web.py/test_render.py),
    exercised here through the real endpoint with nothing mocked away."""

    def _fake_getaddrinfo(host, *args, **kwargs):
        if host == "internal-only.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    resp = client.post(
        f"{API}/clients/onboarding/auto-fill",
        headers=admin_headers,
        json={"name": "Should Still Work", "website": "https://internal-only.example"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "none"
    assert body["client"]["name"] == "Should Still Work"  # user-typed name still used
    assert body["filled"] == [] or "basics.name" in body["filled"]


def test_second_call_is_a_safe_no_op(client: TestClient, admin_headers: dict, monkeypatch):
    from app.utils.render import RenderedPage

    _mock_render(
        monkeypatch,
        RenderedPage(text="Repeat Co text", colors=["#123456"], fonts=[], screenshot=b"x"),
    )
    first = client.post(
        f"{API}/clients/onboarding/auto-fill",
        headers=admin_headers,
        json={"website": "https://repeatco.example"},
    )
    assert first.status_code == 200, first.text
    client_id = first.json()["client"]["id"]

    second = client.post(
        f"{API}/clients/onboarding/auto-fill",
        headers=admin_headers,
        json={"website": "https://repeatco.example", "client_id": client_id},
    )
    assert second.status_code == 200, second.text
    assert second.json()["filled"] == []
