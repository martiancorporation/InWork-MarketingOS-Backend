"""Unit tests for AuditMiddleware helpers: method gating, the submitted-payload
fallback diff (with secret redaction), and request-body buffering + replay."""

from __future__ import annotations

import asyncio

from app.core.middleware import (
    _AUDIT_METHODS,
    _buffer_request_body,
    _changes_from_body,
)


def test_only_mutating_methods_are_audited():
    assert _AUDIT_METHODS == {"POST", "PUT", "PATCH", "DELETE"}
    for read in ("GET", "HEAD", "OPTIONS"):
        assert read not in _AUDIT_METHODS


def test_changes_from_body_builds_before_after_diff():
    changes = _changes_from_body(b'{"name": "Acme", "status": "active"}')
    assert changes == {
        "name": {"before": None, "after": "Acme"},
        "status": {"before": None, "after": "active"},
    }


def test_changes_from_body_redacts_secrets():
    changes = _changes_from_body(b'{"email": "a@b.com", "password": "hunter2", "token": "abc"}')
    assert changes["email"]["after"] == "a@b.com"
    assert changes["password"]["after"] == "***redacted***"
    assert changes["token"]["after"] == "***redacted***"


def test_changes_from_body_ignores_non_objects_and_junk():
    assert _changes_from_body(b"") is None
    assert _changes_from_body(b"not json") is None
    assert _changes_from_body(b"[1, 2, 3]") is None  # not an object
    assert _changes_from_body(b"{}") is None  # empty
    assert _changes_from_body(b'{"x": 1}' + b" " * 70_000) is None  # oversized


def test_buffer_request_body_reads_and_replays():
    incoming = [
        {"type": "http.request", "body": b'{"a":', "more_body": True},
        {"type": "http.request", "body": b"1}", "more_body": False},
    ]

    async def run():
        pending = list(incoming)

        async def receive():
            return pending.pop(0)

        body, replay = await _buffer_request_body(receive, 64_000)
        # The middleware saw the whole body...
        assert body == b'{"a":1}'
        # ...and the app can replay every original message unchanged.
        replayed = [await replay(), await replay()]
        return body, replayed

    body, replayed = asyncio.run(run())
    assert replayed == incoming
