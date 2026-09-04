"""API tests: the security response headers added by SecurityHeadersMiddleware.

The middleware is registered outermost precisely so these land on responses the
inner layers never produce (a 413 from the body-size cap, Starlette's own 500
handler) — a regression there is invisible without asserting on it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API

_EXPECTED = {
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}


def _assert_headers(resp) -> None:
    for name, value in _EXPECTED.items():
        assert resp.headers.get(name) == value, f"{name} missing/wrong on {resp.status_code}"


def test_headers_on_a_successful_response(client: TestClient) -> None:
    _assert_headers(client.get("/health"))


def test_headers_on_an_unauthenticated_error(client: TestClient) -> None:
    resp = client.get(f"{API}/clients")
    assert resp.status_code == 401
    _assert_headers(resp)


def test_headers_on_a_validation_error(client: TestClient, admin_headers: dict) -> None:
    resp = client.get(f"{API}/clients?page=0", headers=admin_headers)
    assert resp.status_code == 422
    _assert_headers(resp)


def test_headers_are_not_duplicated(client: TestClient) -> None:
    """Only added when absent — a proxy that already set a policy keeps it."""
    raw = client.get("/health").headers.get_list("x-frame-options")
    assert raw == ["DENY"]
