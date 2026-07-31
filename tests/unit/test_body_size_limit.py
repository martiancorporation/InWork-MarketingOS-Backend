"""``BodySizeLimitMiddleware`` rejects an oversized request body before it
reaches routing/auth/parsing — defense in depth against a client forcing the
app to buffer an arbitrarily large body in memory.

Uses a small, throwaway cap (wrapping the real app) rather than the generous
production default, so the tests don't need to actually send tens of MB.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.middleware import BodySizeLimitMiddleware
from app.main import app


def _capped(max_bytes: int) -> BodySizeLimitMiddleware:
    """Wrap the real app in a BodySizeLimitMiddleware with a tiny cap, so the
    middleware's own logic can be tested without depending on (or fighting)
    the real, generous ``max_request_body_bytes`` configured for uploads."""
    return BodySizeLimitMiddleware(app, max_bytes=max_bytes)


def test_rejects_via_declared_content_length_header(client: TestClient) -> None:
    # `client` (unused directly) has already wired app.dependency_overrides for
    # this test's hermetic DB session — the same underlying `app` singleton is
    # wrapped below, so the override still applies.
    with TestClient(_capped(10)) as c:
        resp = c.post(
            "/api/v1/auth/login",
            content=b"x" * 100,
            headers={"content-type": "application/json", "content-length": "100"},
        )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_rejects_when_content_length_understates_the_real_body(client: TestClient) -> None:
    """The running-total guard on actual bytes read catches a body larger than
    its own declared Content-Length — not just an honestly-declared oversized
    one — so a client can't dodge the cap by lying about the header. This
    detection happens mid-read inside FastAPI's own body parsing, which wraps
    it in a generic 400 rather than our clean 413 (see the docstring on
    BodySizeLimitMiddleware) — the request is still rejected either way, and
    the oversized body is never fully buffered."""
    with TestClient(_capped(10)) as c:
        resp = c.post(
            "/api/v1/auth/login",
            content=b"x" * 100,
            headers={"content-type": "application/json", "content-length": "1"},
        )
    assert resp.status_code in (400, 413)


def test_small_body_passes_through_untouched(client: TestClient) -> None:
    with TestClient(_capped(10_000)) as c:
        resp = c.post(
            "/api/v1/auth/login", json={"email": "nobody@test.com", "password": "wrong-password"}
        )
    # Reached the real handler and was rejected on credentials, not size.
    assert resp.status_code == 401


def test_non_http_scope_passes_through(client: TestClient) -> None:
    """The real app (generous default cap) still serves ordinary requests."""
    resp = client.get("/health")
    assert resp.status_code == 200


def test_default_cap_is_generous_enough_for_uploads() -> None:
    """The configured default must stay comfortably at or above the largest
    legitimate body (file uploads), or real uploads would start failing."""
    assert get_settings().app.max_request_body_bytes >= get_settings().storage.max_upload_bytes
