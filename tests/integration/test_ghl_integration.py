"""GHL: service-level tests (connect, lazy refresh, tag-scoped contacts fetch)
plus router tests for the two GHL-specific endpoints. GHL is deliberately not
wired into oauth_start/oauth_complete (see integration_service.py) — it never
runs a redirect flow through our app — so the service-level tests exercise
IntegrationService directly rather than via oauth endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.integrations.crypto import TokenCipher
from app.integrations.ghl.client import GhlClient, GhlContactsPage
from app.integrations.ghl.oauth import GhlOAuthClient
from app.models.enums import IntegrationKey, IntegrationStatus
from app.models.integration import Integration
from app.services.integration_service import IntegrationService
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Metro Builders") -> uuid.UUID:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["client"]["id"])


def test_connect_ghl_stores_tokens_encrypted_and_tags(client, admin_headers, db_session: Session):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    integration = svc.connect_ghl(
        cid,
        access_token="access-1",
        refresh_token="refresh-1",
        location_id="p7kd5urztGpYDzzzqH2z",
        tags=["metrobuilders-tampabay", "metrobuilders-memphis"],
        expires_in=3600,
    )

    assert integration.status == IntegrationStatus.connected
    assert integration.external_account_id == "p7kd5urztGpYDzzzqH2z"
    assert integration.ghl_tags == ["metrobuilders-tampabay", "metrobuilders-memphis"]
    assert integration.access_token_encrypted != "access-1"  # never plaintext
    assert TokenCipher().decrypt(integration.access_token_encrypted) == "access-1"
    assert TokenCipher().decrypt(integration.refresh_token_encrypted) == "refresh-1"
    assert integration.token_expires_at is not None

    row = db_session.scalar(
        select(Integration).where(Integration.client_id == cid, Integration.key == IntegrationKey.ghl)
    )
    assert row is not None


def test_connect_ghl_requires_at_least_one_tag(client, admin_headers, db_session: Session):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    with pytest.raises(BadRequestError):
        svc.connect_ghl(
            cid,
            access_token="access-1",
            refresh_token="refresh-1",
            location_id="loc-1",
            tags=[],
        )


def test_connect_ghl_is_idempotent_on_the_same_client(client, admin_headers, db_session: Session):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    svc.connect_ghl(
        cid, access_token="a1", refresh_token="r1", location_id="loc-1", tags=["tony-form-lead"]
    )
    svc.connect_ghl(
        cid, access_token="a2", refresh_token="r2", location_id="loc-1", tags=["tony-form-lead"]
    )

    rows = db_session.scalars(
        select(Integration).where(Integration.client_id == cid, Integration.key == IntegrationKey.ghl)
    ).all()
    assert len(rows) == 1
    assert TokenCipher().decrypt(rows[0].access_token_encrypted) == "a2"


def test_fetch_ghl_contacts_requires_connection(client, admin_headers, db_session: Session):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    with pytest.raises(NotFoundError):
        import asyncio

        asyncio.run(svc.fetch_ghl_contacts(cid))


def test_fetch_ghl_contacts_uses_stored_token_and_tags(
    client, admin_headers, db_session: Session, monkeypatch
):
    import asyncio

    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.connect_ghl(
        cid,
        access_token="fresh-access",
        refresh_token="refresh-1",
        location_id="loc-1",
        tags=["aib-form-lead"],
        expires_in=3600,  # not expiring soon — no refresh should happen
    )

    seen: dict = {}

    async def fake_search(self, token, location_id, tags, *, page_limit=100, search_after=None):
        seen["token"] = token
        seen["location_id"] = location_id
        seen["tags"] = tags
        return GhlContactsPage(contacts=[{"id": "c1", "tags": ["aib-form-lead"]}])

    async def refresh_should_not_be_called(self, refresh_token):
        raise AssertionError("token is fresh — refresh must not be called")

    monkeypatch.setattr(GhlClient, "search_contacts", fake_search)
    monkeypatch.setattr(GhlOAuthClient, "refresh_access_token", refresh_should_not_be_called)

    page: GhlContactsPage = asyncio.run(svc.fetch_ghl_contacts(cid))

    assert seen["token"] == "fresh-access"
    assert seen["location_id"] == "loc-1"
    assert seen["tags"] == ["aib-form-lead"]
    assert [c["id"] for c in page.contacts] == ["c1"]

    db_session.refresh(svc.get(cid, IntegrationKey.ghl))
    assert svc.get(cid, IntegrationKey.ghl).last_sync_at is not None


def test_fetch_ghl_contacts_refreshes_near_expiry_and_persists_rotated_refresh_token(
    client, admin_headers, db_session: Session, monkeypatch
):
    import asyncio

    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    integration = svc.connect_ghl(
        cid,
        access_token="stale-access",
        refresh_token="old-refresh",
        location_id="loc-1",
        tags=["somi-form-lead"],
    )
    # Force it into "about to expire" territory.
    integration.token_expires_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.commit()

    async def fake_refresh(self, refresh_token):
        assert refresh_token == "old-refresh"
        return {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_in": 3600}

    async def fake_search(self, token, location_id, tags, *, page_limit=100, search_after=None):
        assert token == "rotated-access"
        return GhlContactsPage()

    monkeypatch.setattr(GhlOAuthClient, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(GhlClient, "search_contacts", fake_search)

    asyncio.run(svc.fetch_ghl_contacts(cid))

    refreshed = svc.get(cid, IntegrationKey.ghl)
    assert TokenCipher().decrypt(refreshed.access_token_encrypted) == "rotated-access"
    # GHL rotates the refresh token on every use — the OLD one must not survive.
    assert TokenCipher().decrypt(refreshed.refresh_token_encrypted) == "rotated-refresh"


def test_fetch_ghl_contacts_marks_needs_reauth_on_dead_refresh_grant(
    client, admin_headers, db_session: Session, monkeypatch
):
    """GHL refresh tokens are valid up to a year unused, per the account-access
    email thread — once one is actually dead, that must surface as
    `needs_reauth` (reconnect), not the generic `error` a transient failure
    gets, mirroring how Meta/Google's `sync()` already splits the two."""
    import asyncio

    from app.core.exceptions import ProviderAuthError

    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    integration = svc.connect_ghl(
        cid, access_token="stale-access", refresh_token="dead-refresh", location_id="loc-1",
        tags=["tony-form-lead"],
    )
    integration.token_expires_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.commit()

    async def dead_refresh(self, refresh_token):
        raise ProviderAuthError("GHL rejected the token request: invalid_grant")

    monkeypatch.setattr(GhlOAuthClient, "refresh_access_token", dead_refresh)

    with pytest.raises(ProviderAuthError):
        asyncio.run(svc.fetch_ghl_contacts(cid))

    refreshed = svc.get(cid, IntegrationKey.ghl)
    assert refreshed.status == IntegrationStatus.needs_reauth
    assert "invalid_grant" in refreshed.last_error


def test_fetch_ghl_contacts_marks_error_on_transient_failure(
    client, admin_headers, db_session: Session, monkeypatch
):
    """A non-auth failure (network blip, rate limit) stays `error`, not
    `needs_reauth` — an operator shouldn't be told to reconnect for something
    a retry would fix."""
    import asyncio

    from app.core.exceptions import AppError

    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.connect_ghl(
        cid, access_token="tok", refresh_token="refresh", location_id="loc-1",
        tags=["tony-form-lead"], expires_in=3600,
    )

    async def unreachable(self, token, location_id, tags, *, page_limit=100, search_after=None):
        raise AppError("Could not reach GHL: timeout", code="ghl_unreachable", status_code=502)

    monkeypatch.setattr(GhlClient, "search_contacts", unreachable)

    with pytest.raises(AppError):
        asyncio.run(svc.fetch_ghl_contacts(cid))

    refreshed = svc.get(cid, IntegrationKey.ghl)
    assert refreshed.status == IntegrationStatus.error
    assert "timeout" in refreshed.last_error


def test_fetch_ghl_contacts_clears_prior_error_on_success(
    client, admin_headers, db_session: Session, monkeypatch
):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    integration = svc.connect_ghl(
        cid, access_token="tok", refresh_token="refresh", location_id="loc-1",
        tags=["tony-form-lead"], expires_in=3600,
    )
    integration.status = IntegrationStatus.error
    integration.last_error = "Could not reach GHL: timeout"
    db_session.commit()

    async def fake_search(self, token, location_id, tags, *, page_limit=100, search_after=None):
        return GhlContactsPage()

    monkeypatch.setattr(GhlClient, "search_contacts", fake_search)

    import asyncio

    asyncio.run(svc.fetch_ghl_contacts(cid))

    refreshed = svc.get(cid, IntegrationKey.ghl)
    assert refreshed.status == IntegrationStatus.connected
    assert refreshed.last_error is None


def test_fetch_ghl_contacts_requires_tags_configured(
    client, admin_headers, db_session: Session
):
    import asyncio

    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    integration = svc.connect_ghl(
        cid, access_token="a1", refresh_token="r1", location_id="loc-1", tags=["placeholder"]
    )
    integration.ghl_tags = None
    db_session.commit()

    with pytest.raises(BadRequestError):
        asyncio.run(svc.fetch_ghl_contacts(cid))


# ---- router tests: the two GHL-specific HTTP endpoints -------------------- #


def test_connect_endpoint_stores_connection(client, admin_headers):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ghl/connect",
        headers=admin_headers,
        json={
            "access_token": "tok-1",
            "refresh_token": "refresh-1",
            "location_id": "p7kd5urztGpYDzzzqH2z",
            "tags": ["metrobuilders-tampabay", "metrobuilders-memphis"],
            "expires_in": 3600,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "ghl"
    assert body["status"] == "connected"
    assert body["external_account_id"] == "p7kd5urztGpYDzzzqH2z"
    assert body["ghl_tags"] == ["metrobuilders-tampabay", "metrobuilders-memphis"]
    # Never leak the raw token in the response.
    assert "tok-1" not in resp.text


def test_connect_endpoint_rejects_empty_tags(client, admin_headers):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ghl/connect",
        headers=admin_headers,
        json={"access_token": "tok-1", "refresh_token": None, "location_id": "loc-1", "tags": []},
    )
    assert resp.status_code == 422


def test_connect_endpoint_rejects_unknown_fields(client, admin_headers):
    """StrictModel — a typo'd field must 422 loudly, not silently no-op."""
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ghl/connect",
        headers=admin_headers,
        json={
            "access_token": "tok-1",
            "location_id": "loc-1",
            "tags": ["tony-form-lead"],
            "locationid": "typo",
        },
    )
    assert resp.status_code == 422


def test_connect_endpoint_requires_auth(client):
    resp = client.post(
        f"{API}/clients/{uuid.uuid4()}/integrations/ghl/connect",
        json={"access_token": "t", "location_id": "l", "tags": ["x"]},
    )
    assert resp.status_code == 401


def test_contacts_endpoint_returns_normalized_page_and_cursor(client, admin_headers, monkeypatch):
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/integrations/ghl/connect",
        headers=admin_headers,
        json={
            "access_token": "tok-1",
            "location_id": "loc-1",
            "tags": ["aib-form-lead"],
            "expires_in": 3600,
        },
    )

    async def fake_search(self, token, location_id, tags, *, page_limit=100, search_after=None):
        return GhlContactsPage(
            contacts=[
                {
                    "id": "cXyZ1a2B3c4D5e6F7g8H",
                    "contactName": "Jane Sample",
                    "email": "jane.sample@example.com",
                    "phone": "+15551234567",
                    "tags": ["aib-form-lead"],
                    "dateAdded": "2026-09-10T14:22:00.000Z",
                    "source": "Website Form",
                }
            ],
            next_search_after=["cursor-token", "cXyZ1a2B3c4D5e6F7g8H"],
        )

    monkeypatch.setattr(GhlClient, "search_contacts", fake_search)

    resp = client.get(f"{API}/clients/{cid}/integrations/ghl/contacts", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["contacts"]) == 1
    contact = body["contacts"][0]
    assert contact["contact_name"] == "Jane Sample"
    assert contact["date_added"] == "2026-09-10T14:22:00.000Z"
    assert body["next_search_after"]  # opaque cursor string, non-empty

    # The cursor round-trips: passing it back decodes to the original list.
    seen_cursor = {}

    async def capture_cursor(self, token, location_id, tags, *, page_limit=100, search_after=None):
        seen_cursor["value"] = search_after
        return GhlContactsPage()

    monkeypatch.setattr(GhlClient, "search_contacts", capture_cursor)
    resp2 = client.get(
        f"{API}/clients/{cid}/integrations/ghl/contacts",
        headers=admin_headers,
        params={"search_after": body["next_search_after"]},
    )
    assert resp2.status_code == 200, resp2.text
    assert seen_cursor["value"] == ["cursor-token", "cXyZ1a2B3c4D5e6F7g8H"]


def test_contacts_endpoint_rejects_garbage_cursor(client, admin_headers):
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/integrations/ghl/connect",
        headers=admin_headers,
        json={"access_token": "tok-1", "location_id": "loc-1", "tags": ["tony-form-lead"]},
    )
    resp = client.get(
        f"{API}/clients/{cid}/integrations/ghl/contacts",
        headers=admin_headers,
        params={"search_after": "not-valid-base64-json!!!"},
    )
    assert resp.status_code == 400


def test_contacts_endpoint_requires_connection(client, admin_headers):
    cid = _client_id(client, admin_headers)
    resp = client.get(f"{API}/clients/{cid}/integrations/ghl/contacts", headers=admin_headers)
    assert resp.status_code in (400, 404)
