"""GHL: the one agency-wide OAuth2 connection (service + admin router tests),
per-client tag configuration, tag-scoped contacts fetch, and the daily
lead-count sync that feeds ``analytics_daily``.

GHL's real setup for this engagement is one shared location across every
client — so, unlike Meta/Google, its OAuth connect flow is agency-wide
(``IntegrationService.ghl_oauth_start/complete``, the ``/admin/ghl/...``
router), not per-client. The only per-client GHL setting is which tags
identify that client's records within the one shared location
(``set_ghl_tags``)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, BadRequestError, NotFoundError, ProviderAuthError
from app.core.security import hash_password
from app.integrations.crypto import TokenCipher
from app.integrations.ghl.client import GhlClient, GhlContactsPage
from app.integrations.ghl.oauth import GhlOAuthClient
from app.models.enums import IntegrationKey, IntegrationStatus, SocialPlatform, UserRole
from app.models.ghl_agency_connection import GhlAgencyConnection
from app.models.integration import Integration
from app.models.user import User
from app.services.integration_service import IntegrationService
from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Metro Builders") -> uuid.UUID:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["client"]["id"])


def _make_user(db_session: Session, *, email="someone@test.com") -> User:
    user = User(
        email=email, name="Someone", password_hash=hash_password("pass1234"), role=UserRole.admin
    )
    db_session.add(user)
    db_session.commit()
    return user


def _connect_agency(db_session: Session, *, location_id="loc-1") -> GhlAgencyConnection:
    """Directly seed a connected agency connection — the service-level tests
    below exercise per-client behavior against an already-connected agency,
    not the OAuth handshake itself (that's covered separately)."""
    connection = GhlAgencyConnection(
        status=IntegrationStatus.connected,
        location_id=location_id,
        company_id="company-1",
        access_token_encrypted=TokenCipher().encrypt("agency-access"),
        refresh_token_encrypted=TokenCipher().encrypt("agency-refresh"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(connection)
    db_session.commit()
    return connection


# ---- agency-wide OAuth connect flow (service-level) ----------------------- #


def test_ghl_oauth_start_requires_configured_credentials(db_session: Session):
    svc = IntegrationService(db_session)
    from app.core.exceptions import ServiceUnavailableError

    with pytest.raises(ServiceUnavailableError):
        svc.ghl_oauth_start()


def test_ghl_oauth_start_builds_authorize_url_and_marks_pending(db_session: Session, monkeypatch):
    settings = _configure_ghl(monkeypatch)
    svc = IntegrationService(db_session)

    url, state = svc.ghl_oauth_start()

    assert url.startswith(settings.ghl_authorize_url)
    assert "client_id=" + settings.ghl_client_id in url
    assert state
    connection = svc.ghl_agency_repo.get_singleton()
    assert connection is not None
    assert connection.status == IntegrationStatus.pending


def test_ghl_oauth_complete_stores_tokens_encrypted(db_session: Session, monkeypatch):
    _configure_ghl(monkeypatch)
    svc = IntegrationService(db_session)
    _url, state = svc.ghl_oauth_start()

    async def fake_exchange(self, code):
        assert code == "auth-code-1"
        return {
            "access_token": "agency-access",
            "refresh_token": "agency-refresh",
            "expires_in": 3600,
            "locationId": "loc-xyz",
            "companyId": "company-xyz",
        }

    monkeypatch.setattr(GhlOAuthClient, "exchange_code", fake_exchange)
    admin_id = _make_user(db_session).id

    connection = asyncio.run(svc.ghl_oauth_complete("auth-code-1", state, actor_user_id=admin_id))

    assert connection.status == IntegrationStatus.connected
    assert connection.location_id == "loc-xyz"
    assert connection.company_id == "company-xyz"
    assert connection.connected_by == admin_id
    assert TokenCipher().decrypt(connection.access_token_encrypted) == "agency-access"
    assert TokenCipher().decrypt(connection.refresh_token_encrypted) == "agency-refresh"

    # Only ever one row, even across repeated connects.
    rows = db_session.scalars(select(GhlAgencyConnection)).all()
    assert len(rows) == 1


def test_ghl_oauth_complete_rejects_invalid_state(db_session: Session, monkeypatch):
    _configure_ghl(monkeypatch)
    svc = IntegrationService(db_session)
    with pytest.raises(BadRequestError):
        asyncio.run(svc.ghl_oauth_complete("code", "garbage-state", actor_user_id=uuid.uuid4()))


def test_ghl_disconnect_clears_stored_tokens(db_session: Session):
    _connect_agency(db_session)
    svc = IntegrationService(db_session)

    connection = svc.ghl_disconnect()

    assert connection.status == IntegrationStatus.disconnected
    assert connection.access_token_encrypted is None
    assert connection.refresh_token_encrypted is None


def test_ghl_disconnect_without_a_connection_404s(db_session: Session):
    svc = IntegrationService(db_session)
    with pytest.raises(NotFoundError):
        svc.ghl_disconnect()


# ---- admin router: /admin/ghl --------------------------------------------- #


def test_admin_ghl_status_disconnected_when_never_connected(client, admin_headers):
    resp = client.get(f"{API}/admin/ghl", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body is not None
    assert body["status"] == "disconnected"
    assert body["id"] is None


def test_admin_ghl_oauth_start_requires_admin(client, make_user, monkeypatch):
    _configure_ghl(monkeypatch)
    _user, user_headers = make_user()
    resp = client.post(f"{API}/admin/ghl/oauth/start", headers=user_headers)
    assert resp.status_code == 403


def test_admin_ghl_oauth_start_and_complete_round_trip(client, admin_headers, monkeypatch):
    _configure_ghl(monkeypatch)

    start = client.post(f"{API}/admin/ghl/oauth/start", headers=admin_headers)
    assert start.status_code == 200, start.text
    state = start.json()["state"]
    assert start.json()["authorization_url"]

    async def fake_exchange(self, code):
        return {
            "access_token": "should-never-leak-access-token-value",
            "refresh_token": "should-never-leak-refresh-token-value",
            "expires_in": 3600,
            "locationId": "loc-1",
            "companyId": "co-1",
        }

    monkeypatch.setattr(GhlOAuthClient, "exchange_code", fake_exchange)

    complete = client.post(
        f"{API}/admin/ghl/oauth/complete",
        headers=admin_headers,
        json={"code": "auth-code", "state": state},
    )
    assert complete.status_code == 200, complete.text
    body = complete.json()
    assert body["status"] == "connected"
    assert body["location_id"] == "loc-1"
    assert "should-never-leak" not in complete.text  # never leak the raw token

    status = client.get(f"{API}/admin/ghl", headers=admin_headers)
    assert status.json()["status"] == "connected"


def test_admin_ghl_disconnect(client, admin_headers, db_session: Session):
    _connect_agency(db_session)
    resp = client.post(f"{API}/admin/ghl/disconnect", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "disconnected"


# ---- per-client tags + contacts fetch -------------------------------------- #


def test_set_ghl_tags_requires_at_least_one(client, admin_headers, db_session: Session):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    with pytest.raises(BadRequestError):
        svc.set_ghl_tags(cid, [])


def test_set_ghl_tags_is_connected_once_agency_is_connected(
    client, admin_headers, db_session: Session
):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    integration = svc.set_ghl_tags(cid, ["metrobuilders-tampabay"])

    assert integration.status == IntegrationStatus.connected
    assert integration.ghl_tags == ["metrobuilders-tampabay"]
    # No token fields on the per-client row anymore — the credential lives
    # solely on the one agency-wide connection.
    assert integration.access_token_encrypted is None


def test_set_ghl_tags_before_agency_connected_stays_pending(
    client, admin_headers, db_session: Session
):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    integration = svc.set_ghl_tags(cid, ["tony-form-lead"])

    assert integration.status == IntegrationStatus.pending


def test_set_ghl_tags_is_idempotent_on_the_same_client(client, admin_headers, db_session: Session):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    svc.set_ghl_tags(cid, ["a"])
    svc.set_ghl_tags(cid, ["b"])

    rows = db_session.scalars(
        select(Integration).where(
            Integration.client_id == cid, Integration.key == IntegrationKey.ghl
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].ghl_tags == ["b"]


def test_fetch_ghl_contacts_requires_tags_configured(client, admin_headers, db_session: Session):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)

    with pytest.raises(NotFoundError):  # never even connected -> 404, not 400
        asyncio.run(svc.fetch_ghl_contacts(cid))


def test_fetch_ghl_contacts_requires_agency_connected(client, admin_headers, db_session: Session):
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.set_ghl_tags(cid, ["tony-form-lead"])

    with pytest.raises(BadRequestError):
        asyncio.run(svc.fetch_ghl_contacts(cid))


def test_fetch_ghl_contacts_uses_shared_agency_token_and_client_tags(
    client, admin_headers, db_session: Session, monkeypatch
):
    _connect_agency(db_session, location_id="loc-shared")
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.set_ghl_tags(cid, ["aib-form-lead"])

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

    page = asyncio.run(svc.fetch_ghl_contacts(cid))

    assert seen["token"] == "agency-access"
    assert seen["location_id"] == "loc-shared"
    assert seen["tags"] == ["aib-form-lead"]
    assert [c["id"] for c in page.contacts] == ["c1"]


def test_fetch_ghl_contacts_refreshes_the_shared_connection_near_expiry(
    client, admin_headers, db_session: Session, monkeypatch
):
    connection = _connect_agency(db_session)
    connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.commit()
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.set_ghl_tags(cid, ["somi-form-lead"])

    async def fake_refresh(self, refresh_token):
        assert refresh_token == "agency-refresh"
        return {
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }

    async def fake_search(self, token, location_id, tags, *, page_limit=100, search_after=None):
        assert token == "rotated-access"
        return GhlContactsPage()

    monkeypatch.setattr(GhlOAuthClient, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(GhlClient, "search_contacts", fake_search)

    asyncio.run(svc.fetch_ghl_contacts(cid))

    refreshed = svc.ghl_agency_repo.get_singleton()
    assert TokenCipher().decrypt(refreshed.access_token_encrypted) == "rotated-access"
    # GHL rotates the refresh token on every use — the OLD one must not survive.
    assert TokenCipher().decrypt(refreshed.refresh_token_encrypted) == "rotated-refresh"


def test_fetch_ghl_contacts_refresh_benefits_every_client_at_once(
    client, admin_headers, db_session: Session, monkeypatch
):
    """The whole point of the singleton fix: once ANY client's fetch
    refreshes the one shared connection, every OTHER client's next fetch
    sees the already-rotated token too — no per-client copy to go stale."""
    connection = _connect_agency(db_session)
    connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.commit()
    svc = IntegrationService(db_session)
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    svc.set_ghl_tags(cid_a, ["a-tag"])
    svc.set_ghl_tags(cid_b, ["b-tag"])

    async def fake_refresh(self, refresh_token):
        return {
            "access_token": "rotated-once",
            "refresh_token": "rotated-once-r",
            "expires_in": 3600,
        }

    seen_tokens: list[str] = []

    async def fake_search(self, token, location_id, tags, *, page_limit=100, search_after=None):
        seen_tokens.append(token)
        return GhlContactsPage()

    monkeypatch.setattr(GhlOAuthClient, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(GhlClient, "search_contacts", fake_search)

    asyncio.run(svc.fetch_ghl_contacts(cid_a))  # triggers the refresh
    asyncio.run(svc.fetch_ghl_contacts(cid_b))  # must reuse the already-rotated token

    assert seen_tokens == ["rotated-once", "rotated-once"]


def test_fetch_ghl_contacts_marks_needs_reauth_on_dead_refresh_grant(
    client, admin_headers, db_session: Session, monkeypatch
):
    connection = _connect_agency(db_session)
    connection.token_expires_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.commit()
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.set_ghl_tags(cid, ["tony-form-lead"])

    async def dead_refresh(self, refresh_token):
        raise ProviderAuthError("GHL rejected the token request: invalid_grant")

    monkeypatch.setattr(GhlOAuthClient, "refresh_access_token", dead_refresh)

    with pytest.raises(ProviderAuthError):
        asyncio.run(svc.fetch_ghl_contacts(cid))

    connection_after = svc.ghl_agency_repo.get_singleton()
    assert connection_after.status == IntegrationStatus.needs_reauth
    integration_after = svc.get(cid, IntegrationKey.ghl)
    assert integration_after.status == IntegrationStatus.needs_reauth
    assert "invalid_grant" in integration_after.last_error


def test_fetch_ghl_contacts_marks_error_on_transient_failure(
    client, admin_headers, db_session: Session, monkeypatch
):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.set_ghl_tags(cid, ["tony-form-lead"])

    async def unreachable(self, token, location_id, tags, *, page_limit=100, search_after=None):
        raise AppError("Could not reach GHL: timeout", code="ghl_unreachable", status_code=502)

    monkeypatch.setattr(GhlClient, "search_contacts", unreachable)

    with pytest.raises(AppError):
        asyncio.run(svc.fetch_ghl_contacts(cid))

    integration_after = svc.get(cid, IntegrationKey.ghl)
    assert integration_after.status == IntegrationStatus.error
    assert "timeout" in integration_after.last_error


def test_fetch_ghl_contacts_clears_prior_error_on_success(
    client, admin_headers, db_session: Session, monkeypatch
):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    integration = svc.set_ghl_tags(cid, ["tony-form-lead"])
    integration.status = IntegrationStatus.error
    integration.last_error = "Could not reach GHL: timeout"
    db_session.commit()

    async def fake_search(self, token, location_id, tags, *, page_limit=100, search_after=None):
        return GhlContactsPage()

    monkeypatch.setattr(GhlClient, "search_contacts", fake_search)

    asyncio.run(svc.fetch_ghl_contacts(cid))

    refreshed = svc.get(cid, IntegrationKey.ghl)
    assert refreshed.status == IntegrationStatus.connected
    assert refreshed.last_error is None


# ---- lead-count sync (-> analytics_daily) --------------------------------- #


def test_sync_ghl_leads_counts_contacts_per_day(
    client, admin_headers, db_session: Session, monkeypatch
):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.set_ghl_tags(cid, ["aib-form-lead"])

    today = datetime.now(UTC)
    contacts = [
        {"id": "c1", "dateAdded": today.isoformat()},
        {"id": "c2", "dateAdded": today.isoformat()},
        {"id": "c3", "dateAdded": (today - timedelta(days=1)).isoformat()},
    ]

    async def fake_search_all(self, token, location_id, tags):
        return contacts

    monkeypatch.setattr(GhlClient, "search_all_contacts", fake_search_all)

    written = asyncio.run(svc.sync_ghl_leads(cid))

    assert written == 2  # two distinct days got a row
    from app.repositories.analytics_repository import AnalyticsRepository

    rows, _total = AnalyticsRepository(db_session).list_daily(
        cid, start=(today - timedelta(days=2)).date(), end=today.date(), platform=SocialPlatform.ghl
    )
    by_date = {r.date: r.leads for r in rows}
    assert by_date[today.date()] == 2
    assert by_date[(today - timedelta(days=1)).date()] == 1


def test_sync_ghl_leads_requires_tags(client, admin_headers, db_session: Session):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    with pytest.raises(NotFoundError):  # never even configured -> 404, not 400
        asyncio.run(svc.sync_ghl_leads(cid))


def test_sync_ghl_leads_ignores_malformed_dates(
    client, admin_headers, db_session: Session, monkeypatch
):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    svc = IntegrationService(db_session)
    svc.set_ghl_tags(cid, ["aib-form-lead"])

    async def fake_search_all(self, token, location_id, tags):
        return [{"id": "c1", "dateAdded": "not-a-date"}, {"id": "c2", "dateAdded": None}]

    monkeypatch.setattr(GhlClient, "search_all_contacts", fake_search_all)

    written = asyncio.run(svc.sync_ghl_leads(cid))
    assert written == 0


# ---- per-client router: /clients/{id}/integrations/ghl/{tags,contacts} --- #


def test_set_tags_endpoint(client, admin_headers, db_session: Session):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ghl/tags",
        headers=admin_headers,
        json={"tags": ["metrobuilders-tampabay", "metrobuilders-memphis"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "ghl"
    assert body["status"] == "connected"
    assert body["ghl_tags"] == ["metrobuilders-tampabay", "metrobuilders-memphis"]


def test_set_tags_endpoint_rejects_empty_tags(client, admin_headers):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ghl/tags", headers=admin_headers, json={"tags": []}
    )
    assert resp.status_code == 422


def test_set_tags_endpoint_rejects_unknown_fields(client, admin_headers):
    """StrictModel — a typo'd field must 422 loudly, not silently no-op."""
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/integrations/ghl/tags",
        headers=admin_headers,
        json={"tags": ["tony-form-lead"], "location_id": "typo-not-a-real-field"},
    )
    assert resp.status_code == 422


def test_set_tags_endpoint_requires_auth(client):
    resp = client.post(f"{API}/clients/{uuid.uuid4()}/integrations/ghl/tags", json={"tags": ["x"]})
    assert resp.status_code == 401


def test_contacts_endpoint_returns_normalized_page_and_cursor(
    client, admin_headers, db_session: Session, monkeypatch
):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/integrations/ghl/tags",
        headers=admin_headers,
        json={"tags": ["aib-form-lead"]},
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


def test_contacts_endpoint_rejects_garbage_cursor(client, admin_headers, db_session: Session):
    _connect_agency(db_session)
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/integrations/ghl/tags",
        headers=admin_headers,
        json={"tags": ["tony-form-lead"]},
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


def _configure_ghl(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings().integrations
    monkeypatch.setattr(settings, "ghl_client_id", "client-123")
    monkeypatch.setattr(settings, "ghl_client_secret", "secret-456")
    monkeypatch.setattr(settings, "ghl_redirect_uri", "https://app.inwork.com/oauth/ghl/callback")
    return settings
