"""Unit tests: GHL/LeadConnector client — request shape, pagination, tag
membership, and error classification. All network calls are faked."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.config import get_settings
from app.core.exceptions import AppError, ProviderAuthError, ServiceUnavailableError
from app.integrations.ghl.client import GhlClient, contact_has_tag
from app.integrations.ghl.oauth import GhlOAuthClient


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """Records every POST body it receives; replays queued responses in order."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def post(self, url, *, headers=None, json=None, data=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "data": data})
        return self._responses.pop(0)


def _run(coro):
    return asyncio.run(coro)


# ---- GhlClient.search_contacts: request shape ---------------------------- #


def test_search_contacts_builds_or_filter_group_across_tags(monkeypatch):
    fake = _FakeHttp([_FakeResponse({"contacts": []})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    _run(
        GhlClient().search_contacts(
            "token-abc", "loc-1", ["aib-form-lead", "somi-form-lead"], page_limit=100
        )
    )

    body = fake.calls[0]["json"]
    assert body["locationId"] == "loc-1"
    assert body["pageLimit"] == 100
    assert body["filters"] == [
        {
            "group": "OR",
            "filters": [
                {"field": "tags", "operator": "eq", "value": "aib-form-lead"},
                {"field": "tags", "operator": "eq", "value": "somi-form-lead"},
            ],
        }
    ]
    assert "searchAfter" not in body
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer token-abc"
    assert fake.calls[0]["headers"]["Version"] == get_settings().integrations.ghl_api_version


def test_search_contacts_requires_at_least_one_tag():
    with pytest.raises(AppError):
        _run(GhlClient().search_contacts("token", "loc-1", []))


def test_search_contacts_forwards_search_after_cursor(monkeypatch):
    fake = _FakeHttp([_FakeResponse({"contacts": []})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    _run(
        GhlClient().search_contacts(
            "token", "loc-1", ["tony-form-lead"], search_after=["cursor-val", "id-123"]
        )
    )

    assert fake.calls[0]["json"]["searchAfter"] == ["cursor-val", "id-123"]


# ---- pagination ------------------------------------------------------------ #


def test_search_all_contacts_walks_search_after_until_a_short_page(monkeypatch):
    page1 = {
        "contacts": [{"id": f"c{i}", "tags": ["tony-form-lead"], "searchAfter": [i]} for i in range(2)]
    }
    page2 = {"contacts": [{"id": "c-last", "tags": ["tony-form-lead"], "searchAfter": [99]}]}
    fake = _FakeHttp([_FakeResponse(page1), _FakeResponse(page2)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    contacts = _run(
        GhlClient().search_all_contacts("token", "loc-1", ["tony-form-lead"], page_limit=2)
    )

    assert [c["id"] for c in contacts] == ["c0", "c1", "c-last"]
    # Second call continues from the first page's cursor.
    assert fake.calls[1]["json"]["searchAfter"] == [1]


def test_search_all_contacts_stops_when_next_cursor_is_absent(monkeypatch):
    fake = _FakeHttp([_FakeResponse({"contacts": [{"id": "only-one", "tags": []}]})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    contacts = _run(GhlClient().search_all_contacts("token", "loc-1", ["tony-form-lead"]))

    assert len(contacts) == 1
    assert len(fake.calls) == 1  # no second page requested


# ---- error classification -------------------------------------------------- #


def test_search_contacts_maps_401_to_provider_auth_error(monkeypatch):
    fake = _FakeHttp([_FakeResponse({"message": "invalid token"}, status_code=401)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    with pytest.raises(ProviderAuthError):
        _run(GhlClient().search_contacts("bad-token", "loc-1", ["aib-form-lead"]))


def test_search_contacts_maps_other_errors_to_app_error(monkeypatch):
    fake = _FakeHttp([_FakeResponse({"message": "bad request"}, status_code=400)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    with pytest.raises(AppError) as seen:
        _run(GhlClient().search_contacts("token", "loc-1", ["aib-form-lead"]))
    assert not isinstance(seen.value, ProviderAuthError)


# ---- contact_has_tag: never assume a contact has only the one tag -------- #


def test_contact_has_tag_checks_membership_not_equality():
    contact = {"id": "c1", "tags": ["aib-form-lead", "newsletter-subscriber"]}
    assert contact_has_tag(contact, "aib-form-lead") is True
    assert contact_has_tag(contact, "somi-form-lead") is False


def test_contact_has_tag_handles_missing_tags_key():
    assert contact_has_tag({"id": "c1"}, "aib-form-lead") is False


# ---- GhlOAuthClient.refresh_access_token ---------------------------------- #


@pytest.fixture
def ghl_configured(monkeypatch):
    s = get_settings().integrations
    monkeypatch.setattr(s, "ghl_client_id", "client-123")
    monkeypatch.setattr(s, "ghl_client_secret", "secret-456")
    return s


def test_refresh_unconfigured_raises_service_unavailable():
    with pytest.raises(ServiceUnavailableError):
        _run(GhlOAuthClient().refresh_access_token("refresh-tok"))


def test_refresh_success_returns_rotated_tokens(monkeypatch, ghl_configured):
    fake = _FakeHttp(
        [
            _FakeResponse(
                {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
            )
        ]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    tokens = _run(GhlOAuthClient().refresh_access_token("old-refresh"))

    assert tokens["access_token"] == "new-access"
    assert tokens["refresh_token"] == "new-refresh"
    assert fake.calls[0]["data"]["grant_type"] == "refresh_token"
    assert fake.calls[0]["data"]["refresh_token"] == "old-refresh"
    assert fake.calls[0]["data"]["client_id"] == "client-123"


def test_refresh_rejected_grant_raises_provider_auth_error(monkeypatch, ghl_configured):
    fake = _FakeHttp([_FakeResponse({"error": "invalid_grant"}, status_code=400)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)

    with pytest.raises(ProviderAuthError):
        _run(GhlOAuthClient().refresh_access_token("dead-refresh"))
